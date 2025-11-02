import os
from llama_cpp import Llama
from llama_cpp.llama_chat_format import (
    Llava15ChatHandler, Llava16ChatHandler, MoondreamChatHandler,
    NanoLlavaChatHandler, Llama3VisionAlphaChatHandler, MiniCPMv26ChatHandler,
    Qwen25VLChatHandler, Qwen3VLChatHandler
)
import comfy.model_management as mm
import comfy.utils
import folder_paths
import torch
import numpy as np
from PIL import Image
import base64
import io
import gc

llm_extensions = ['.ckpt', '.pt', '.bin', '.pth', '.safetensors', '.gguf']
script_directory = os.path.dirname(os.path.abspath(__file__))
folder_paths.folder_names_and_paths["LLM"] = ([os.path.join(folder_paths.models_dir, "LLM")], llm_extensions)

def parse_json(json_output: str) -> str:
    if "```json" in json_output:
        json_output = json_output.split("```json", 1)[1]
        json_output = json_output.split("```", 1)[0]
        
    try:
        parsed = json.loads(json_output)
        if isinstance(parsed, dict) and "content" in parsed:
            inner = parsed["content"]
            if isinstance(inner, str):
                json_output = inner
    except Exception:
        pass
    return json_output

def get_chat_handler(model_type):
    match model_type:
        case "Qwen3-VL":
            return Qwen3VLChatHandler
        case "Qwen2.5-VL":
            return Qwen25VLChatHandler
        case "LLaVA-1.5":
            return Llava15ChatHandler
        case "LLaVA-1.6":
            return Llava16ChatHandler
        case "Moondream2":
            return MoondreamChatHandler
        case "nanoLLaVA":
            return NanoLlavaChatHandler
        case "llama3-Vision-Alpha":
            return Llama3VisionAlphaChatHandler
        case "MiniCPM-v2.6":
            return MiniCPMv26ChatHandler
        case "MiniCPM-v4":
            return MiniCPMv26ChatHandler
        case "None":
            return None
        case _:
            raise ValueError(f'Unknow model type: "{model_type}"')

class llama_cpp_model_loader:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "model": (folder_paths.get_filename_list("LLM"),),
            "mmproj_model": (["None"]+folder_paths.get_filename_list("LLM"), {"default": "None"}),
            "mmodel_type": (["None","Qwen3-VL", "Qwen2.5-VL", "LLaVA-1.5", "LLaVA-1.6", "Moondream2", "nanoLLaVA", "llama3-Vision-Alpha", "MiniCPM-v2.6", "MiniCPM-v4"], {"default": "None"}),
            "think_mode": ("BOOLEAN", {"default": False}),
            "n_ctx": ("INT", {"default": 2048, "min": 512, "max": 327680, "step": 128}),
            "n_gpu_layers": ("INT", {"default": -1, "min": -1, "max": 4096, "step": 1}),
            }
        }

    RETURN_TYPES = ("LLAMACPPMODEL",)
    RETURN_NAMES = ("llamamodel",)
    FUNCTION = "loadmodel"
    CATEGORY = "Llama-cpp-vl"

    def loadmodel(self, model, mmproj_model, mmodel_type, think_mode, n_ctx, n_gpu_layers):
        custom_config = {"model": model, "mmproj_model": mmproj_model, "mmodel_type":mmodel_type, "think_mode": think_mode, "n_ctx": n_ctx, "n_gpu_layers": n_gpu_layers}
        return (custom_config,)

class llama_cpp_instruct:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "llmamodel": ("LLAMACPPMODEL",),
                "parameters": ("LLAMACPPARAMS",),
                "prompt": ("STRING", {"multiline": True, "default": "",}),
                "system_prompt": ("STRING", {"multiline": True, "default": ""}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "foce_offload": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "image": ("IMAGE",)
            }
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output",)
    FUNCTION = "process"
    CATEGORY = "Llama-cpp-vl"
    
    def process(self, llmamodel, parameters, prompt, system_prompt, seed, foce_offload, image=None):
        mm.soft_empty_cache()
        
        model = llmamodel["model"]
        mmproj_model = llmamodel["mmproj_model"]
        mmodel_type = llmamodel["mmodel_type"]
        think_mode = llmamodel["think_mode"]
        n_ctx = llmamodel["n_ctx"]
        n_gpu_layers = llmamodel["n_gpu_layers"]
        
        if not hasattr(self, "llm") or self.current_config != llmamodel:
            if hasattr(self, "llm"):
                self.llm.close()
                try:
                    self.chat_handler._exit_stack.close()
                except Exception:
                    pass
            model_path = os.path.join(folder_paths.models_dir, 'LLM', model)
            self.current_config = llmamodel
            self.chat_handler = None
            if mmproj_model and mmproj_model != "None":
                mmproj_path = os.path.join(folder_paths.models_dir, 'LLM', mmproj_model)
                if mmodel_type == "None":
                    raise ValueError('"mmodel_type" cannot be None!')
                print(f"Loading mmproj from {mmproj_path}")
                handler = get_chat_handler(mmodel_type)
                if "Qwen3" in mmodel_type:
                    self.chat_handler = handler(clip_model_path=mmproj_path, use_think_prompt=think_mode, verbose=False)
                else:
                    self.chat_handler = handler(clip_model_path=mmproj_path, verbose=False)
            print(f"Loading model from {model_path}")
            self.llm = Llama(model_path, chat_handler=self.chat_handler, n_gpu_layers=n_gpu_layers, n_ctx=n_ctx, verbose=False)
            
        messages = []
        
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
            
        user_content = []
        
        if image is not None:
            if not hasattr(self.chat_handler, "clip_model_path") or self.chat_handler.clip_model_path is None:
                 raise ValueError("Image input detected, but the loaded model is not configured with a vision module (mmproj).")
                
            image_np = np.clip(255. * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8)
            pil_image = Image.fromarray(image_np)
            buffered = io.BytesIO()
            pil_image.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            image_content = {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_base64}"}
            }
            user_content.append(image_content)
            
        user_content.append({"type": "text", "text": prompt})
        
        messages.append({"role": "user", "content": user_content})
        
        output = self.llm.create_chat_completion(
            messages=messages,
            seed=seed,
            **parameters
        )
        
        if foce_offload:
            self.llm.close()
            try:
                self.chat_handler._exit_stack.close()
            except Exception:
                pass
            del self.llm, self.chat_handler
            gc.collect()
            mm.soft_empty_cache()
            
        text = output['choices'][0]['message']['content']
        
        if text.startswith(": "):
            text = text[2:]
        text = text.lstrip() 
        
        return (text,)

class llama_cpp_parameters:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
                "max_tokens": ("INT", {"default": 512, "min": 0, "max": 4096, "step": 1}),
                "top_k": ("INT", {"default": 40, "min": 0, "max": 1000, "step": 1}),
                "top_p": ("FLOAT", {"default": 0.95, "min": 0.0, "max": 1.0, "step": 0.01}),
                "min_p": ("FLOAT", {"default": 0.05, "min": 0.0, "max": 1.0, "step": 0.01}),
                "typical_p": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.01}),
                "repeat_penalty": ("FLOAT", {"default": 1.1, "min": 0.0, "max": 10.0, "step": 0.01}),
                "frequency_penalty": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "presence_penalty": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 2.0, "step": 0.01}),
                #"tfs_z": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mirostat_mode": ("INT", {"default": 0, "min": 0, "max": 2, "step": 1}),
                "mirostat_eta": ("FLOAT", {"default": 0.1, "min": 0.0, "max": 1.0, "step": 0.01}),
                "mirostat_tau": ("FLOAT", {"default": 5.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                }
        }
    RETURN_TYPES = ("LLAMACPPARAMS",)
    RETURN_NAMES = ("parameters",)
    FUNCTION = "process"
    CATEGORY = "Llama-cpp-vl"
    def process(self, **kwargs):
        return (kwargs,)

class json_to_bbox:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "json": ("STRING", {"forceInput": True}),
            },
        }
    
    RETURN_TYPES = ("BBOX",)
    RETURN_NAMES = ("output",)
    FUNCTION = "process"
    CATEGORY = "Llama-cpp-vl"
    
    def process(self, json):
        bboxs = parse_json(json)
        return(bboxs,)

NODE_CLASS_MAPPINGS = {
    "llama_cpp_model_loader": llama_cpp_model_loader,
    "llama_cpp_instruct": llama_cpp_instruct,
    "llama_cpp_parameters": llama_cpp_parameters
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "llama_cpp_model_loader": "Llama-cpp Model Loader",
    "llama_cpp_instruct": "Llama-cpp Instruct",
    "llama_cpp_parameters": "Llama-cpp Parameters"
}