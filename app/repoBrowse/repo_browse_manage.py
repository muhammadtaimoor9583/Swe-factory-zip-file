import os
from typing import Dict, List
from app.api import agent_browse_file
from loguru import logger
class RepoBrowseManager:
    def __init__(self, project_path: str):
        self.project_path = os.path.abspath(project_path)  # Ensure absolute path
        self.index: Dict = {}
        self._build_index()

    def _build_index(self):
        """Build the index by parsing the repository structure."""
        self._update_index(self.project_path)

    def _update_index(self, current_path: str):
        """Recursively update the index with files and directories."""
        for root, dirs, files in os.walk(current_path):
            relative_root = os.path.relpath(root, self.project_path)
            current_level = self.index
            if relative_root != ".":  # Handle nested directories
                for part in relative_root.split(os.sep):
                    if part not in current_level:
                        current_level[part] = {}
                    current_level = current_level[part]
            for file in files:
                current_level[file] = None  # Mark files as leaf nodes

    def browse_folder(self, path: str, depth: int) -> tuple[str, str, bool]:
        """Browse a folder in the repository from the given path and depth.
        
        Args:
            path: The folder path to browse, relative to the project root
            depth: How many levels deep to browse the folder structure
            
        Returns:
            A formatted string showing the folder structure
            
        Raises:
            ValueError: If the path is outside the project directory
        """
        if not path or path == "/":
            abs_path = self.project_path
        else:
            # Check if the path is an absolute path
            if os.path.isabs(path):
                abs_path = path  # If absolute, use it directly
            else:
                # If relative path, join with project root and convert to absolute
                abs_path = os.path.abspath(os.path.join(self.project_path, path))
    

        if not abs_path.startswith(self.project_path):
            return 'Path does not exist', 'Path does not exist',False
          
        
        relative_path = os.path.relpath(abs_path, self.project_path)
        if relative_path == ".":
            current_level = self.index
        else:
            current_level = self.index
            for part in relative_path.split(os.sep):
                if part not in current_level:
                    return "Path not found", "Path not found", False  # Path not found
                current_level = current_level[part]
        
        structure_result = self._get_structure(current_level, depth)
        structure = self._format_structure(structure_result)
        result = f"You are browsing the path: {abs_path}. The browsing Depth is {depth}.\nStructure of this path:\n\n{self._format_structure(structure_result)}"

        return result, 'folder structure collected', True


    def search_files_by_keyword(self, keyword: str) -> tuple[str, str, bool]:
        """Search for files in the repository whose names contain the given keyword.
        
        Args:
            keyword: The keyword to search for in file names
            
        Returns:
            tuple: (formatted result string, summary message, success flag)
        """
        matching_files = []
        self._search_index(self.index, keyword, "", matching_files)
        
        if not matching_files:
            return f"No files found containing the keyword '{keyword}'.", "No matching files found", True

        max_files = 50
        if len(matching_files) > max_files:
            result = f"Found {len(matching_files)} files containing the keyword '{keyword}'. Showing the first {max_files}:\n\n"
            matching_files = matching_files[:max_files]
        else:
            result = f"Found {len(matching_files)} files containing the keyword '{keyword}':\n\n"
        
        formatted_files = "\n".join([f"- {os.path.normpath(file)}" for file in matching_files])
        result += formatted_files
        return result, "File search completed successfully", True

    def _search_index(self, current_level: Dict, keyword: str, current_path: str, matching_files: List[str]):
        """Recursively search the index for files containing the keyword in their names."""
        for key, value in current_level.items():
            new_path = os.path.join(current_path, key)
            if value is None:  # It's a file
                if keyword.lower() in key.lower():
                    matching_files.append(new_path)
            else:  # It's a directory
                self._search_index(value, keyword, new_path, matching_files)

    def _get_structure(self, structure: Dict, depth: int) -> Dict:
        """Get the structure of the repository from the given path and depth."""
        if depth == 0:
            return {}
        result = {}
        for key, value in structure.items():
            if value is None:  # It's a file
                result[key] = None
            else:  # It's a directory
                result[key] = self._get_structure(value, depth - 1)
        return result

    def _format_structure(self, structure: Dict, indent: int = 0) -> str:
        """Format the structure into a string with proper indentation."""
        result = ""
        for key, value in structure.items():
            if value is None:  # It's a file
                result += "    " * indent + key + "\n\n"
            else:  # It's a directory
                result += "    " * indent + key + "/\n\n"
                result += self._format_structure(value, indent + 1)
        return result

    def browse_file(self, file_path: str) -> str:
        """Browse and return the contents of a specific file.
        
        Args:
            file_path: The path to the file to browse, relative to the project root
            
        Returns:
            The contents of the file as a string
            
        Raises:
            ValueError: If the file path is outside the project directory
            FileNotFoundError: If the file does not exist
        """
        # abs_path = os.path.abspath(file_path)
        # if not abs_path.startswith(self.project_path):
        #     raise ValueError(f"The file path {file_path} is not within the project path {self.project_path}.")
            
        # if not os.path.isfile(abs_path):
        #     raise FileNotFoundError(f"File not found: {file_path}")
            
        with open(file_path, 'r') as file:
            if file_path.endswith('package-lock.json'):
                # 读取前1000行
                lines = itertools.islice(file, 1000)
                content = ''.join(lines)
                content +='\nTruncated this file because it is too long.'
            else:
                content = file.read()
        return content

    def get_webpage_content(self, url: str, timeout: int = 60) -> str:
        """Fetch and return the content of a web page using Jina Reader API.
        
        Args:
            url: The URL of the web page to fetch
            timeout: Maximum time in seconds to wait for the response (default: 10)
            
        Returns:
            The content of the web page as a string
            
        Raises:
            ValueError: If the URL is invalid or the request fails
            TimeoutError: If the request times out
        """
        if not url.startswith(('http://', 'https://')):
            raise ValueError("Invalid URL - must start with http:// or https://")
            
        jina_reader_url = f"https://r.jina.ai/{url}"
        
        try:
            response = requests.get(jina_reader_url, timeout=timeout)
            response.raise_for_status()
            
            # Validate content type
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' not in content_type and 'text/plain' not in content_type:
                raise ValueError(f"Unsupported content type: {content_type}")
                
            return response.text
            
        except requests.exceptions.Timeout:
            raise TimeoutError(f"Request timed out after {timeout} seconds")
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Failed to fetch web content: {str(e)}")
        
    def browse_file_for_environment_info(self, file_path: str) -> tuple[str, str, bool]:
        """Browse a file and extract environment setup information.
        
        Args:
            repo_browse_manager: Instance for managing repo browsing.
            file_path: The path to the file to browse, relative to the project root.
            
        Returns:
            A string containing extracted environment setup info.
        """
        try:
            logger.info('entering browse')
            # Step 1: Browse the file content
            file_content = self.browse_file(file_path)
            logger.info(f"{file_content}")
            file_content = f"The content of {file_path} is:\n"+file_content 
            # Step 2: Use LLM to extract environment information
            extracted_info = agent_browse_file.browse_file_run_with_retries(file_content)

            # Step 3: Return extracted information
            return extracted_info,'Get File Info', True

        except ValueError as e:
            logger.info(f"Invalid file path: {str(e)}")
            return 'Invalid file path:','Invalid file path:', False
            
            # raise
        except FileNotFoundError as e:
            logger.info(f"File not found: {str(e)}")
            return 'File not found','File not found', False
            
            # raise
        except Exception as e:
            logger.info(f"Unexpected error browsing file: {str(e)}")
            return 'Unexpected error browsing file','Unexpected error browsing file', False
            
            # raise RuntimeError(f"Failed to browse file: {str(e)}") from e


    def browse_webpage_for_environment_info(self, url: str) -> str:
        """Fetch a web page and extract environment setup information.
        
        Args:
            repo_browse_manager: Instance for managing repo browsing.
            url: The URL of the web page to fetch and analyze.
            
        Returns:
            A string containing extracted environment setup info.
        """
        try:
            # Step 1: Fetch the webpage content
            webpage_content = self.get_webpage_content(url)
            
            # Step 2: Use LLM to extract environment information
            extracted_info = agent_browse_file.browse_file_run_with_retries(webpage_content)

            # Step 3: Return extracted information
            return extracted_info, 'Get Web Info', True

    
        except Exception as e:
            logger.info(f"Unexpected error browsing webpage: {str(e)}")
            return 'Unexpected error browsing web','Unexpected error browsing web', False

project_path = "/path/to/your/project"
repo_manager = RepoBrowseManager('/home/azureuser/glh/redis-py')
print(repo_manager.browse_folder("/home/azureuser/glh/redis-py", 2)[0])
print(repo_manager.search_files_by_keyword('readme'))
print(repo_manager.browse_file('/home/azureuser/glh/redis-py/CHANGES'))