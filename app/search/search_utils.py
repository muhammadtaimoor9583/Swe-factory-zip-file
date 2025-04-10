import ast
import glob
import pathlib
import re
from dataclasses import dataclass
from os.path import join as pjoin
from tree_sitter import Language, Parser
from app import utils as apputils
import os
import json
import subprocess
import sys
@dataclass
class SearchResult:
    """Dataclass to hold search results."""

    file_path: str  # this is absolute path
    class_name: str | None
    func_name: str | None
    code: str

    def to_tagged_upto_file(self, project_root: str):
        """Convert the search result to a tagged string, upto file path."""
        rel_path = apputils.to_relative_path(self.file_path, project_root)
        file_part = f"<file>{rel_path}</file>"
        return file_part

    def to_tagged_upto_class(self, project_root: str):
        """Convert the search result to a tagged string, upto class."""
        prefix = self.to_tagged_upto_file(project_root)
        class_part = (
            f"<class>{self.class_name}</class>" if self.class_name is not None else ""
        )
        return f"{prefix}\n{class_part}"

    def to_tagged_upto_func(self, project_root: str):
        """Convert the search result to a tagged string, upto function."""
        prefix = self.to_tagged_upto_class(project_root)
        func_part = (
            f" <func>{self.func_name}</func>" if self.func_name is not None else ""
        )
        return f"{prefix}{func_part}"

    def to_tagged_str(self, project_root: str):
        """Convert the search result to a tagged string."""
        prefix = self.to_tagged_upto_func(project_root)
        code_part = f"<code>\n{self.code}\n</code>"
        return f"{prefix}\n{code_part}"

    @staticmethod
    def collapse_to_file_level(lst, project_root: str) -> str:
        """Collapse search results to file level."""
        res = dict()  # file -> count
        for r in lst:
            if r.file_path not in res:
                res[r.file_path] = 1
            else:
                res[r.file_path] += 1
        res_str = ""
        for file_path, count in res.items():
            rel_path = apputils.to_relative_path(file_path, project_root)
            file_part = f"<file>{rel_path}</file>"
            res_str += f"- {file_part} ({count} matches)\n"
        return res_str

    @staticmethod
    def collapse_to_method_level(lst, project_root: str) -> str:
        """Collapse search results to method level."""
        res = dict()  # file -> dict(method -> count)
        for r in lst:
            if r.file_path not in res:
                res[r.file_path] = dict()
            func_str = r.func_name if r.func_name is not None else "Not in a function"
            if func_str not in res[r.file_path]:
                res[r.file_path][func_str] = 1
            else:
                res[r.file_path][func_str] += 1
        res_str = ""
        for file_path, funcs in res.items():
            rel_path = apputils.to_relative_path(file_path, project_root)
            file_part = f"<file>{rel_path}</file>"
            for func, count in funcs.items():
                if func == "Not in a function":
                    func_part = func
                else:
                    func_part = f" <func>{func}</func>"
                res_str += f"- {file_part}{func_part} ({count} matches)\n"
        return res_str


def find_python_files(dir_path: str) -> list[str]:
    """Get all .py files recursively from a directory.

    Skips files that are obviously not from the source code, such third-party library code.

    Args:
        dir_path (str): Path to the directory.
    Returns:
        List[str]: List of .py file paths. These paths are ABSOLUTE path!
    """

    py_files = glob.glob(pjoin(dir_path, "**/*.py"), recursive=True)
    res = []
    for file in py_files:
        rel_path = file[len(dir_path) + 1 :]
        if rel_path.startswith("build"):
            continue
        if rel_path.startswith("doc"):
            # discovered this issue in 'pytest-dev__pytest'
            continue
        if rel_path.startswith("requests/packages"):
            # to walkaround issue in 'psf__requests'
            continue
        if (
            rel_path.startswith("tests/regrtest_data")
            or rel_path.startswith("tests/input")
            or rel_path.startswith("tests/functional")
        ):
            # to walkaround issue in 'pylint-dev__pylint'
            continue
        if rel_path.startswith("tests/roots") or rel_path.startswith(
            "sphinx/templates/latex"
        ):
            # to walkaround issue in 'sphinx-doc__sphinx'
            continue
        if rel_path.startswith("tests/test_runner_apps/tagged/") or rel_path.startswith(
            "django/conf/app_template/"
        ):
            # to walkaround issue in 'django__django'
            continue
        res.append(file)
    return res

def find_java_files(dir_path: str) -> list[str]:
    """Get all .py files recursively from a directory.

    Skips files that are obviously not from the source code, such third-party library code.

    Args:
        dir_path (str): Path to the directory.
    Returns:
        List[str]: List of .py file paths. These paths are ABSOLUTE path!
    """

    java_files = glob.glob(pjoin(dir_path, "**/*.java"), recursive=True)
    res = []
    for file in java_files:
        rel_path = file[len(dir_path) + 1 :]
        if (
            rel_path.startswith("gson/src/test")
            or rel_path.startswith("test-jpms")
            or rel_path.startswith("test-shrinker")
            or rel_path.startswith("test-graal-native-image")
            ):
             # discovered this issue in 'gson__gson'
            continue
        if rel_path.startswith('assertj-core/src/test'):
            continue
        if 'src/test' in rel_path:
            continue
    
       
        res.append(file)
    return res

def find_javascript_files(dir_path: str) -> list[str]:
    """Get all .py files recursively from a directory.

    Skips files that are obviously not from the source code, such third-party library code.

    Args:
        dir_path (str): Path to the directory.
    Returns:
        List[str]: List of .py file paths. These paths are ABSOLUTE path!
    """

    js_files = glob.glob(pjoin(dir_path, "**/*.js"), recursive=True)
    ts_files = glob.glob(pjoin(dir_path, "**/*.ts"), recursive=True)


    # 将所有文件合并
    all_files = js_files + ts_files 

    res = []
    for file in all_files:
        rel_path = file[len(dir_path) + 1 :]
        if (
            rel_path.endswith("test.ts")
            or rel_path.endswith("test.js")
            ):
             
            continue
        if (
            rel_path.startswith("test/")
            or rel_path.startswith("tests/")
            ):
             
            continue
        if (
            rel_path.startswith("changelog_unreleased")
            or rel_path.startswith("docs")
            or rel_path.startswith("website")
            or rel_path.startswith("doc")
            or rel_path.startswith("examples")
            ):
             
            continue
        if 'test/' in rel_path or 'tests/' in rel_path:
            continue
    
     
        res.append(file)
    return res

def parse_java_file(file_full_path: str) -> tuple[list, dict, list] | None:
  
    try:
        file_content = pathlib.Path(file_full_path).read_text()
    except Exception as e:
      
        return None
   

    try:
        JAVA_LANGUAGE = Language('pl_plugin/java.so', 'java')
        parser = Parser()
        parser.set_language(JAVA_LANGUAGE)
        tree = parser.parse(bytes(file_content, 'utf8'))
        root_node = tree.root_node
        # (1) get all classes defined in the file
        classes = []
        # (2) for each class in the file, get all functions defined in the class.
        class_to_funcs = {}
        # (3) get top-level functions in the file (exclues functions defined in classes)
        top_level_funcs = []

        def traverse(node):
            # nonlocal classes, class_to_funcs, top_level_funcs

            if node.type == 'class_declaration':

                class_name_node = node.child_by_field_name('name')
                class_name = class_name_node.text.decode('utf8') if class_name_node else 'AnonymousClass'

                start_line = node.start_point[0] + 1  
                end_line = node.end_point[0] + 1

                # 初始化方法列表
                class_methods = []

                # 找到类体
                body_node = node.child_by_field_name('body')
                if body_node:
                    # 遍历类体的成员
                    for member in body_node.children:
                        if member.type == 'method_declaration':
                            # 获取方法名
                            method_name_node = member.child_by_field_name('name')
                            method_name = method_name_node.text.decode('utf8') if method_name_node else 'AnonymousMethod'

                            # 获取方法的起始和结束行号
                            method_start = member.start_point[0] + 1
                            method_end = member.end_point[0] + 1

                            # 添加方法信息
                            class_methods.append((method_name,method_start,method_end))

                        elif member.type == 'constructor_declaration':
                            # 获取构造函数名称
                            method_name_node = member.child_by_field_name('name')
                            method_name = method_name_node.text.decode('utf8') if method_name_node else class_name

                            # 获取构造函数的起始和结束行号
                            method_start = member.start_point[0] + 1
                            method_end = member.end_point[0] + 1

                            # 添加构造函数信息
                            class_methods.append((method_name,method_start,method_end))
                            

                # 添加类信息
                classes.append((class_name,start_line,end_line))

                class_to_funcs[class_name] = class_methods



            for child in node.children:
                traverse(child)

        traverse(root_node)

    except Exception as e:

        print(f"Parsing Error: {e}")
        return None

    return classes, class_to_funcs, top_level_funcs

def parse_javascript_file(file_full_path: str) -> tuple[list, dict, list] | None:
    try:
        file_content = pathlib.Path(file_full_path).read_text()
    except Exception as e:
      
        return None
       
    try:
        result = subprocess.run(
            ['node', 'pl_plugin/parse_js.js'],
            input=file_content ,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )
       
        data = json.loads(result.stdout)
        
        classes = data['classes']
        class_to_funcs = data['class_to_funcs']
        top_level_funcs = data['top_level_funcs']
        return classes, class_to_funcs, top_level_funcs
    except subprocess.CalledProcessError as e:
        print(f"Error when executing Node Js script: {e}", file=sys.stderr)
        print(f"stderr: {e.stderr}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"Error in parsing json: {e}", file=sys.stderr)
        print(f"Output content: {result.stdout}", file=sys.stderr)
        return None
    

def parse_python_file(file_full_path: str) -> tuple[list, dict, list] | None:
    """
    Main method to parse AST and build search index.
    Handles complication where python ast module cannot parse a file.
    """
    try:
        file_content = pathlib.Path(file_full_path).read_text()
        tree = ast.parse(file_content)
    except Exception:
        # failed to read/parse one file, we should ignore it
        return None

    # (1) get all classes defined in the file
    classes = []
    # (2) for each class in the file, get all functions defined in the class.
    class_to_funcs = {}
    # (3) get top-level functions in the file (exclues functions defined in classes)
    top_level_funcs = []

    function_nodes_in_class = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            ## class part (1): collect class info
            class_name = node.name
            start_lineno = node.lineno
            end_lineno = node.end_lineno
            # line numbers are 1-based
            classes.append((class_name, start_lineno, end_lineno))

            ## class part (2): collect function info inside this class
            class_funcs = []
            for n in ast.walk(node):
                if isinstance(n, ast.FunctionDef):
                    class_funcs.append((n.name, n.lineno, n.end_lineno))
                    function_nodes_in_class.append(n)
            class_to_funcs[class_name] = class_funcs

        # top-level functions, excluding functions defined in classes
        elif isinstance(node, ast.FunctionDef) and node not in function_nodes_in_class:
            function_name = node.name
            start_lineno = node.lineno
            end_lineno = node.end_lineno
            # line numbers are 1-based
            top_level_funcs.append((function_name, start_lineno, end_lineno))

    return classes, class_to_funcs, top_level_funcs


def get_func_snippet_in_class(
    file_full_path: str, class_name: str, func_name: str, include_lineno=False
) -> str | None:
    """Get actual function source code in class.

    All source code of the function is returned.
    Assumption: the class and function exist.
    """
    with open(file_full_path) as f:
        file_content = f.read()

    tree = ast.parse(file_content)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for n in ast.walk(node):
                if isinstance(n, ast.FunctionDef) and n.name == func_name:
                    start_lineno = n.lineno
                    end_lineno = n.end_lineno
                    assert end_lineno is not None, "end_lineno is None"
                    if include_lineno:
                        return get_code_snippets_with_lineno(
                            file_full_path, start_lineno, end_lineno
                        )
                    else:
                        return get_code_snippets(
                            file_full_path, start_lineno, end_lineno
                        )
    # In this file, cannot find either the class, or a function within the class
    return None


def get_code_region_containing_code(
    file_full_path: str, code_str: str
) -> list[tuple[int, str]]:
    """In a file, get the region of code that contains a specific string.

    Args:
        - file_full_path: Path to the file. (absolute path)
        - code_str: The string that the function should contain.
    Returns:
        - A list of tuple, each of them is a pair of (line_no, code_snippet).
        line_no is the starting line of the matched code; code snippet is the
        source code of the searched region.
    """
    with open(file_full_path) as f:
        file_content = f.read()

    context_size = 3
    # since the code_str may contain multiple lines, let's not split the source file.

    # we want a few lines before and after the matched string. Since the matched string
    # can also contain new lines, this is a bit trickier.
    pattern = re.compile(re.escape(code_str))
    # each occurrence is a tuple of (line_no, code_snippet) (1-based line number)
    occurrences: list[tuple[int, str]] = []
    file_content_lines = file_content.splitlines()
    for match in pattern.finditer(file_content):
        matched_start_pos = match.start()
        # first, find the line number of the matched start position (0-based)
        matched_line_no = file_content.count("\n", 0, matched_start_pos)

        window_start_index = max(0, matched_line_no - context_size)
        window_end_index = min(
            len(file_content_lines), matched_line_no + context_size + 1
        )

        context = "\n".join(file_content_lines[window_start_index:window_end_index])
        occurrences.append((matched_line_no, context))

    return occurrences


def get_func_snippet_with_code_in_file(file_full_path: str, code_str: str) -> list[str]:
    """In a file, get the function code, for which the function contains a specific string.

    Args:
        file_full_path (str): Path to the file. (absolute path)
        code_str (str): The string that the function should contain.

    Returns:
        A list of code snippets, each of them is the source code of the searched function.
    """
    with open(file_full_path) as f:
        file_content = f.read()

    tree = ast.parse(file_content)
    all_snippets = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        func_start_lineno = node.lineno
        func_end_lineno = node.end_lineno
        assert func_end_lineno is not None
        func_code = get_code_snippets(
            file_full_path, func_start_lineno, func_end_lineno
        )
        # This func code is a raw concatenation of source lines which contains new lines and tabs.
        # For the purpose of searching, we remove all spaces and new lines in the code and the
        # search string, to avoid non-match due to difference in formatting.
        stripped_func = " ".join(func_code.split())
        stripped_code_str = " ".join(code_str.split())
        if stripped_code_str in stripped_func:
            all_snippets.append(func_code)

    return all_snippets


def get_code_snippets_with_lineno(file_full_path: str, start: int, end: int) -> str:
    """Get the code snippet in the range in the file.

    The code snippet should come with line number at the beginning for each line.

    TODO: When there are too many lines, return only parts of the output.
          For class, this should only involve the signatures.
          For functions, maybe do slicing with dependency analysis?

    Args:
        file_path (str): Path to the file.
        start (int): Start line number. (1-based)
        end (int): End line number. (1-based)
    """
    with open(file_full_path) as f:
        file_content = f.readlines()

    snippet = ""
    for i in range(start - 1, end):
        snippet += f"{i+1} {file_content[i]}"
    return snippet


def get_code_snippets(file_full_path: str, start: int, end: int) -> str:
    """Get the code snippet in the range in the file, without line numbers.

    Args:
        file_path (str): Full path to the file.
        start (int): Start line number. (1-based)
        end (int): End line number. (1-based)
    """
    with open(file_full_path) as f:
        file_content = f.readlines()
    snippet = ""
    for i in range(start - 1, end):
        snippet += file_content[i]
    return snippet


def extract_func_sig_from_ast(func_ast: ast.FunctionDef) -> list[int]:
    """Extract the function signature from the AST node.

    Includes the decorators, method name, and parameters.

    Args:
        func_ast (ast.FunctionDef): AST of the function.

    Returns:
        The source line numbers that contains the function signature.
    """
    func_start_line = func_ast.lineno
    if func_ast.decorator_list:
        # has decorators
        decorator_start_lines = [d.lineno for d in func_ast.decorator_list]
        decorator_first_line = min(decorator_start_lines)
        func_start_line = min(decorator_first_line, func_start_line)
    # decide end line from body
    if func_ast.body:
        # has body
        body_start_line = func_ast.body[0].lineno
        end_line = body_start_line - 1
    else:
        # no body
        end_line = func_ast.end_lineno
    assert end_line is not None
    return list(range(func_start_line, end_line + 1))


def extract_class_sig_from_ast(class_ast: ast.ClassDef) -> list[int]:
    """Extract the class signature from the AST.

    Args:
        class_ast (ast.ClassDef): AST of the class.

    Returns:
        The source line numbers that contains the class signature.
    """
    # STEP (1): extract the class signature
    sig_start_line = class_ast.lineno
    if class_ast.body:
        # has body
        body_start_line = class_ast.body[0].lineno
        sig_end_line = body_start_line - 1
    else:
        # no body
        sig_end_line = class_ast.end_lineno
    assert sig_end_line is not None
    sig_lines = list(range(sig_start_line, sig_end_line + 1))

    # STEP (2): extract the function signatures and assign signatures
    for stmt in class_ast.body:
        if isinstance(stmt, ast.FunctionDef):
            sig_lines.extend(extract_func_sig_from_ast(stmt))
        elif isinstance(stmt, ast.Assign):
            # for Assign, skip some useless cases where the assignment is to create docs
            stmt_str_format = ast.dump(stmt)
            if "__doc__" in stmt_str_format:
                continue
            # otherwise, Assign is easy to handle
            assert stmt.end_lineno is not None
            assign_range = list(range(stmt.lineno, stmt.end_lineno + 1))
            sig_lines.extend(assign_range)

    return sig_lines


def get_class_signature(file_full_path: str, class_name: str,language: str) -> str:
    """Get the class signature.

    Args:
        file_path (str): Path to the file.
        class_name (str): Name of the class.
    """
    with open(file_full_path) as f:
        file_content = f.read()


    if language.lower() == 'python':
        tree = ast.parse(file_content)
        relevant_lines = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                # we reached the target class
                relevant_lines = extract_class_sig_from_ast(node)
                break
        if not relevant_lines:
            return ""
        else:
            with open(file_full_path) as f:
                file_content = f.readlines()
            result = ""
            for line in relevant_lines:
                line_content: str = file_content[line - 1]
                if line_content.strip().startswith("#"):
                    # this kind of comment could be left until this stage.
                    # reason: # comments are not part of func body if they appear at beginning of func
                    continue
                result += line_content
            return result
        
    elif language.lower() == 'java':
        relevant_lines = []
        try:
            # java_parser_path = os.getenv('JAVA_PARSER_PATH')
            JAVA_LANGUAGE = Language('pl_plugin/java.so', 'java')
            parser = Parser()
            parser.set_language(JAVA_LANGUAGE)
            tree = parser.parse(bytes(file_content, 'utf8'))
            root_node = tree.root_node
            relevant_lines = extract_class_sig_from_ast_java(root_node,class_name)
        except Exception as e:
            print(e)
        if not relevant_lines:
            return ""
        else:
            with open(file_full_path) as f:
                file_content = f.readlines()
            result = ""
            for line in relevant_lines:
                line_content: str = file_content[line - 1]
                # line_content = line_content.rstrip('{')
                if line_content.strip().startswith("//"):
                    # this kind of comment could be left until this stage.
                    # reason: # comments are not part of func body if they appear at beginning of func
                    continue
                result += line_content
            return result
    elif language.lower() in ['javascript','typescript']:
        relevant_lines = []
        try:
 
            relevant_lines = extract_class_sig_from_ast_javascript(file_content,class_name)
        except Exception as e:
            print(e)
        if not relevant_lines:
            return ""
        else:
            with open(file_full_path) as f:
                file_content = f.readlines()
            result = ""
            for line_idx in range(len(relevant_lines)):
                line = relevant_lines[line_idx]
                
                line_content: str = file_content[line - 1]
                # line_content = line_content.rstrip('{')
                # if line_content.strip().startswith("//"):
                #     # this kind of comment could be left until this stage.
                #     # reason: # comments are not part of func body if they appear at beginning of func
                #     continue
                result += line_content
                if line_idx+1< len(relevant_lines):
                    next_line_idx = line_idx+1
                    next_line = relevant_lines[next_line_idx]
                    if next_line!= line+1:
                        result+='\n'
            return result
        
def extract_class_signature_from_node_java(node) -> list[int]:
    """从 Java 类节点中提取类签名行数。"""
    signature_lines = []
    stack = [node]  # 初始化栈，开始于类声明节点

    while stack:
        current_node = stack.pop()

        if current_node.type == 'class_declaration':
            class_start_line = current_node.start_point[0] + 1
            class_body = None
            for child in current_node.named_children:
                if child.type == 'class_body':
                    class_body = child
                    break
            if class_body:
                class_body_start_line = class_body.start_point[0] + 1
            
                if class_body_start_line > class_start_line:
                    signature_lines.extend(range(class_start_line, class_body_start_line))
                else:
      
                    signature_lines.append(class_start_line)
            else:
       
                signature_lines.append(class_start_line)


        elif current_node.type in ('method_declaration', 'constructor_declaration'):
  
            body_node = None
            for child in current_node.named_children:
                if child.type == 'block':
                    body_node = child
                    break

            if body_node:
                signature_start_line = current_node.start_point[0] + 1
                signature_end_line = body_node.start_point[0]+1 
                if signature_end_line > signature_start_line:
                    signature_lines.extend(range(signature_start_line, signature_end_line+1))
                else:
                    signature_lines.append(signature_start_line)
            # else:
             
            #     signature_lines.append(current_node.start_point[0] + 1)

        elif current_node.type == 'field_declaration':
            field_start = current_node.start_point[0] + 1
            field_end = current_node.end_point[0] + 1
            signature_lines.extend(range(field_start, field_end + 1))


        for child in reversed(current_node.children):
            if child.is_named:
                stack.append(child)

    return signature_lines

def extract_class_sig_from_ast_java(node, target_name: str) -> list[int]:
    """Recursively find the class node by its name in the Java AST."""
    if node.type == 'class_declaration':
        class_name_node = node.child_by_field_name('name')
        class_name = class_name_node.text.decode('utf8') if class_name_node else 'AnonymousClass'

        if class_name == target_name:
            return extract_class_signature_from_node_java(node)

    # Traverse through the children of this node
    for child in node.children:
        found_class = extract_class_sig_from_ast_java(child, target_name)
        if found_class:
            return found_class
    return []

def extract_class_sig_from_ast_javascript(file_content: str, class_name: str) -> list[int]:
    """Extract the class signature from the AST.

    Args:
        class_ast (ast.ClassDef): AST of the class.

    Returns:
        The source line numbers that contains the class signature.
    """

    try:
        result = subprocess.run(
            ['node', 'pl_plugin/get_class_signature.js'],
            input=file_content ,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )
       
        relevant_lines = json.loads(result.stdout)
        

        return relevant_lines
    except subprocess.CalledProcessError as e:
        print(f"Error when executing Node Js script: {e}", file=sys.stderr)
        print(f"stderr: {e.stderr}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"Error in parsing json: {e}", file=sys.stderr)
        print(f"Output content: {result.stdout}", file=sys.stderr)
        return None
    # STEP (1): extract the class signature
