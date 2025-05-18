## 🚀 Environment Setup
```bash
conda create --name swe-collector python=3.12.5 -y
conda activate swe-collector
pip install -r requirements.txt
```

## Run SWE-collector

```bash
export OPENAI_API_BASE_URL=<your url>
export OPENAI_KEY=<your key>
bash run_gpt_new.sh
```