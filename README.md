# ComfyUI-llama-cpp  
Run LLM/vLLM models natively in ComfyUI based on llama.cpp  
**[[📃中文版](./README_zh.md)]**

## Changelog  
#### 2025-11-03  
- Initial release, added support for Qwen3-VL  

## Preview  
![](./img/preview.jpg)

## Installation  

#### Install the node:  
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lihaoyun6/ComfyUI-llama-cpp.git
python -m pip install -r ComfyUI-llama-cpp/requirements.txt
```

#### Install llama.cpp  
- Download a prebuilt wheel of `llama-cpp-python` from `https://github.com/JamePeng/llama-cpp-python/releases`, or build it from source according to your system.  

#### Download models:  
- Place your model files in the `ComfyUI/models/LLM` folder.  

## Credits  
- [llama-cpp-python](https://github.com/JamePeng/llama-cpp-python) @JamePeng  
- [ComfyUI-llama-cpp](https://github.com/kijai/ComfyUI-llama-cpp) @kijai  
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) @comfyanonymous
