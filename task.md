# Hello!

Complete actions described below for files located in `data` directory 
using your favorite LLM framework.

Extract copyright holder, license name and save them as structured data.

If license type is open source permissive (not GPL or similar):
- extract function names with number of arguments for each function and save them as structured data

If license type open source and copyleft (GPL and similar):
- count number of functions in file
- if number of functions > 2
  - extract function names with number of arguments for each function and save them as structured data
- else
  - use LLM to rewrite file in rust (save result)

Bonus point for:
- Tests
- Dockerfile
  









