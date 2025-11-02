# ComfyUI-llama-cpp
在ComfyUI中基于llama.cpp原生运行LLM/vLLM模型。  
**[[📃English](./readme.md)]**

## 更新日志
#### 2025-11-03
- 首次上传, 支持Qwen3-VL      

## 预览
![](./img/preview.jpg) 

## 安装步骤

#### 安装节点:
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lihaoyun6/ComfyUI-llama-cpp.git
python -m pip install -r ComfyUI-llama-cpp/requirements.txt
```
#### 安装llama.cpp 
- 请从`https://github.com/JamePeng/llama-cpp-python/releases`安装llama-cpp-python的预编译whl, 或根据设备架构自行编译安装.  

#### 模型下载:
- 请将模型文件下载至`ComfyUI/models/LLM`目录中.  

## 致谢
- [llama-cpp-python](https://github.com/JamePeng/llama-cpp-python) @JamePeng  
- [ComfyUI-llama-cpp](https://github.com/kijai/ComfyUI-llama-cpp) @kijai
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) @comfyanonymous
