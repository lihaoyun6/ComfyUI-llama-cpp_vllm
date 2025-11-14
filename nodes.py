import os
import io
import gc
import json
import base64
import random
import torch

import numpy as np
from PIL import Image, ImageDraw

import folder_paths
import comfy.model_management as mm
import comfy.utils

from llama_cpp import Llama
from llama_cpp.llama_chat_format import (
    Llava15ChatHandler, Llava16ChatHandler, MoondreamChatHandler,
    NanoLlavaChatHandler, Llama3VisionAlphaChatHandler, MiniCPMv26ChatHandler,
    Qwen25VLChatHandler, Qwen3VLChatHandler
)

llm_extensions = ['.ckpt', '.pt', '.bin', '.pth', '.safetensors', '.gguf']
folder_paths.folder_names_and_paths["LLM"] = ([os.path.join(folder_paths.models_dir, "LLM")], llm_extensions)
preset_prompts = {
    "Normal - Describe": "Describe this @.",
    "Prompt Style - Tags": "Your task is to generate a clean list of comma-separated tags for a text-to-@ AI, based *only* on the visual information in the @. Limit the output to a maximum of 50 unique tags. Strictly describe visual elements like subject, clothing, environment, colors, lighting, and composition. Do not include abstract concepts, interpretations, marketing terms, or technical jargon (e.g., no 'SEO', 'brand-aligned', 'viral potential'). The goal is a concise list of visual descriptors. Avoid repeating tags.",
    "Prompt Style - Simple": "Analyze the @ and generate a simple, single-sentence text-to-@ prompt. Describe the main subject and the setting concisely.",
    "Prompt Style - Detailed": "Generate a detailed, artistic text-to-@ prompt based on the @. Combine the subject, their actions, the environment, lighting, and overall mood into a single, cohesive paragraph of about 2-3 sentences. Focus on key visual details.",
    "Prompt Style - Extreme Detailed": "Generate an extremely detailed and descriptive text-to-@ prompt from the @. Create a rich paragraph that elaborates on the subject's appearance, textures of clothing, specific background elements, the quality and color of light, shadows, and the overall atmosphere. Aim for a highly descriptive and immersive prompt.",
    "Prompt Style - Cinematic": "Act as a master prompt engineer. Create a highly detailed and evocative prompt for an @ generation AI. Describe the subject, their pose, the environment, the lighting, the mood, and the artistic style (e.g., photorealistic, cinematic, painterly). Weave all elements into a single, natural language paragraph, focusing on visual impact.",
    "Creative - Detailed Analysis": "Describe this @ in detail, breaking down the subject, attire, accessories, background, and composition into separate sections.",
    "Creative - Summarize Video": "Summarize the key events and narrative points in this video.",
    "Creative - Short Story": "Write a short, imaginative story inspired by this @ or video.",
    "Creative - Refine & Expand Prompt": "Refine and enhance the following user prompt for creative text-to-@ generation. Keep the meaning and keywords, make it more expressive and visually rich. Output **only the improved prompt text itself**, without any reasoning steps, thinking process, or additional commentary."
}
preset_tags = list(preset_prompts.keys())

def image2base64(image):
    img = Image.fromarray(image)
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=85)
    img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return img_base64

def parse_json(json_str):
    json_output = json_str.strip().removeprefix("```json").removesuffix("```")
    try:
        parsed = json.loads(json_output)
    except Exception as e:
        raise ValueError(f"Unable to load JSON data!\n{e}")
    return parsed

def scale_image(image: torch.Tensor, max_size: int = 128):
    resized_frames = []
    img_np = np.clip(255.0 * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8)
    img_pil = Image.fromarray(img_np)
    
    w, h = img_pil.size
    scale = min(max_size / max(w, h), 1.0)
    new_w, new_h = int(w * scale), int(h * scale)
    img_resized = img_pil.resize((new_w, new_h), Image.Resampling.LANCZOS)
    
    return np.array(img_resized)

def qwen3bbox(image, json):
    img = Image.fromarray(np.clip(255.0 * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8))
    bboxes = []
    for item in json:
        x0, y0, x1, y1 = item["bbox_2d"]
        size = 1000
        x0 = x0 / size * img.width
        y0 = y0 / size * img.height
        x1 = x1 / size * img.width
        y1 = y1 / size * img.height
        bboxes.append((x0, y0, x1, y1))
    return bboxes

def draw_bbox(image, json, mode):
    label_colors = {}
    img = Image.fromarray(np.clip(255.0 * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    
    for item in json:
        try:
            label = item["label"]
        except Exception:
            try:
                label = item["text_content"]
            except Exception:
                label = "bbox"
        x0, y0, x1, y1 = item["bbox_2d"]
        if mode in ["Qwen3-VL", "Qwen2-VL"]:
            size = 1000
            x0 = x0 / size * img.width
            y0 = y0 / size * img.height
            x1 = x1 / size * img.width
            y1 = y1 / size * img.height
        bbox = (x0, y0, x1, y1)
        
        if label not in label_colors:
            label_colors[label] = tuple(random.randint(80, 180) for _ in range(3))
        color = label_colors[label]
        draw.rectangle(bbox, outline=color, width=4)
        text_y = max(0, y0 - 10)
        text_size = draw.textbbox((x0, text_y), label)
        draw.rectangle([text_size[0], text_size[1]-2, text_size[2]+4, text_size[3]+2], fill=color)
        draw.text((x0+2, text_y), label, fill=(255,255,255))
    return torch.from_numpy(np.array(img).astype(np.float32) / 255.0).unsqueeze(0)

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

def get_model(config):
    model = config["model"]
    mmproj_model = config["mmproj_model"]
    model_type = config["model_type"]
    think_mode = config["think_mode"]
    n_ctx = config["n_ctx"]
    n_gpu_layers = config["n_gpu_layers"]

    model_path = os.path.join(folder_paths.models_dir, 'LLM', model)
    chat_handler = None
    if mmproj_model and mmproj_model != "None":
        mmproj_path = os.path.join(folder_paths.models_dir, 'LLM', mmproj_model)
        if model_type == "None":
            raise ValueError('"model_type" cannot be None!')
        print(f"Loading mmproj from {mmproj_path}")
        handler = get_chat_handler(model_type)
        if model_type == "Qwen3-VL":
            try:
                chat_handler = handler(clip_model_path=mmproj_path, use_think_prompt=think_mode, verbose=False)
            except Exception as e:
                chat_handler = handler(clip_model_path=mmproj_path, force_reasoning=think_mode, verbose=False)
        else:
            chat_handler = handler(clip_model_path=mmproj_path, verbose=False)
    print(f"Loading model from {model_path}")
    llm = Llama(model_path, chat_handler=chat_handler, n_gpu_layers=n_gpu_layers, n_ctx=n_ctx, verbose=False)
    return (chat_handler, llm)

class llama_cpp_model_loader:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "model": (folder_paths.get_filename_list("LLM"),),
            "mmproj_model": (["None"]+folder_paths.get_filename_list("LLM"), {"default": "None"}),
            "model_type": (["None","Qwen3-VL", "Qwen2.5-VL", "LLaVA-1.5", "LLaVA-1.6", "Moondream2", "nanoLLaVA", "llama3-Vision-Alpha", "MiniCPM-v2.6", "MiniCPM-v4"], {"default": "None"}),
            "think_mode": ("BOOLEAN", {"default": False}),
            "n_ctx": ("INT", {"default": 8192, "min": 512, "max": 327680, "step": 128}),
            "n_gpu_layers": ("INT", {"default": -1, "min": -1, "max": 4096, "step": 1}),
            "keep_model_loaded": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("LLAMACPPMODEL",)
    RETURN_NAMES = ("llamamodel",)
    FUNCTION = "loadmodel"
    CATEGORY = "llama-cpp-vllm"

    def loadmodel(self, model, mmproj_model, model_type, think_mode, n_ctx, n_gpu_layers, keep_model_loaded):
        custom_config = {"model": model, "mmproj_model": mmproj_model, "model_type":model_type, "think_mode": think_mode, "n_ctx": n_ctx, "n_gpu_layers": n_gpu_layers, "keep_model_loaded": keep_model_loaded}
        return (custom_config,)

class llama_cpp_instruct_adv:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "llmamodel": ("LLAMACPPMODEL",),
                "parameters": ("LLAMACPPARAMS",),
                "preset_prompt": (preset_tags, {"default": preset_tags[0]}),
                "custom_prompt": ("STRING", {"default": "", "multiline": True, "placeholder": "If provided, this will override the preset prompt."}),
                "system_prompt": ("STRING", {"multiline": True, "default": ""}),
                "video_input": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Treat the input image sequence as video."
                }),
                "max_frames": ("INT", {
                    "default": 24,
                    "min": 2,
                    "max": 1024,
                    "step": 1,
                    "tooltip": "Number of frames to sample evenly from the video."
                }),
                "video_size": ([128, 256, 512, 768, 1024], {
                    "default": 256,
                    "tooltip": "Automatically scale down the video size."
                }),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "step": 1}),
            },
            "optional": {
                "images": ("IMAGE",),
            }
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output",)
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    
    def process(self, llmamodel, parameters, preset_prompt, custom_prompt, system_prompt, video_input, max_frames, video_size, seed, images=None):
        mm.soft_empty_cache()
        keep_model_loaded = llmamodel['keep_model_loaded']

        if not hasattr(self, "llm") or self.current_config != llmamodel:
            if hasattr(self, "llm"):
                self.llm.close()
                try:
                    self.chat_handler._exit_stack.close()
                except Exception:
                    pass
            self.current_config = llmamodel
            self.chat_handler, self.llm = get_model(llmamodel)
            
        messages = []
        
        system_prompts = "请将输入的图片序列当做视频而不是静态帧序列, " + system_prompt if video_input else system_prompt
        if system_prompts.strip():
            messages.append({"role": "system", "content": system_prompts})
            
        user_content = []
        if custom_prompt.strip():
            user_content.append({"type": "text", "text": custom_prompt})
        else:
            user_content.append({"type": "text", "text": preset_prompts[preset_prompt].replace("@", "video" if video_input else "image")})
            
        if images is not None:
            if not hasattr(self.chat_handler, "clip_model_path") or self.chat_handler.clip_model_path is None:
                 raise ValueError("Image input detected, but the loaded model is not configured with a vision module (mmproj).")
            
            frames = images
            if video_input:
                indices = np.linspace(0, len(images) - 1, max_frames, dtype=int)
                frames = [images[i] for i in indices]
                
            for image in frames:
                if video_input:
                    data = image2base64(scale_image(image, video_size))
                else:
                    data = image2base64(np.clip(255.0 * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8))
                image_content = {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{data}"}
                }
                user_content.append(image_content)

        messages.append({"role": "user", "content": user_content})
        
        output = self.llm.create_chat_completion(messages=messages, seed=seed, **parameters)
        text = output['choices'][0]['message']['content']
        text = text[2:].lstrip() if text.startswith(": ") else text.lstrip() 
        
        if not keep_model_loaded:
            self.llm.close()
            try:
                self.chat_handler._exit_stack.close()
            except Exception:
                pass
            del self.llm, self.chat_handler
            gc.collect()
            mm.soft_empty_cache()
        
        return (text,)

class llama_cpp_instruct:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "llmamodel": ("LLAMACPPMODEL",),
                "parameters": ("LLAMACPPARAMS",),
                "prompt": ("STRING", {"multiline": True, "default": "",}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "step": 1}),
            },
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output",)
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    
    def process(self, llmamodel, parameters, prompt, seed):
        mm.soft_empty_cache()
        keep_model_loaded = llmamodel['keep_model_loaded']
        
        if not hasattr(self, "llm") or self.current_config != llmamodel:
            if hasattr(self, "llm"):
                self.llm.close()
                try:
                    self.chat_handler._exit_stack.close()
                except Exception:
                    pass
            self.current_config = llmamodel
            self.chat_handler, self.llm = get_model(llmamodel)
            
        messages = []
        user_content = []
        user_content.append({"type": "text", "text": prompt})
        messages.append({"role": "user", "content": user_content})
        
        output = self.llm.create_chat_completion(
            messages=messages,
            seed=seed,
            **parameters
        )
        
        if not keep_model_loaded:
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
                "top_k": ("INT", {"default": 30, "min": 0, "max": 1000, "step": 1}),
                "top_p": ("FLOAT", {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.01}),
                "min_p": ("FLOAT", {"default": 0.05, "min": 0.0, "max": 1.0, "step": 0.01}),
                "typical_p": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "temperature": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 2.0, "step": 0.01}),
                "repeat_penalty": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "frequency_penalty": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "presence_penalty": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.01}),
                #"tfs_z": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mirostat_mode": ("INT", {"default": 0, "min": 0, "max": 2, "step": 1}),
                "mirostat_eta": ("FLOAT", {"default": 0.1, "min": 0.0, "max": 1.0, "step": 0.01}),
                "mirostat_tau": ("FLOAT", {"default": 5.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                }
        }
    RETURN_TYPES = ("LLAMACPPARAMS",)
    RETURN_NAMES = ("parameters",)
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    def process(self, **kwargs):
        return (kwargs,)

class json_to_bbox:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "json": ("STRING", {"forceInput": True}),
                "mode": (["simple","Qwen3-VL", "Qwen2-VL"], {"default": "simple"}),
            },
            "optional": {
                "image": ("IMAGE",),
            }
        }
    
    RETURN_TYPES = ("BBOX", "IMAGE")
    RETURN_NAMES = ("bbox", "image")
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    
    def process(self, json, mode, image=None):
        bboxes = parse_json(json)
        if image is not None:
            image = draw_bbox(image, bboxes, mode)
        if mode in ["Qwen3-VL", "Qwen2-VL"]:
            if image is None:
                raise ValueError(f'When using the "{mode}" mode, the original input image must be connected!')
            bbox = qwen3bbox(image, bboxes)
        else:
            bbox = [tuple(item["bbox_2d"]) for item in bboxes]
        return(bbox, image,)

NODE_CLASS_MAPPINGS = {
    "llama_cpp_model_loader": llama_cpp_model_loader,
    "llama_cpp_instruct_adv": llama_cpp_instruct_adv,
    "llama_cpp_instruct": llama_cpp_instruct,
    "llama_cpp_parameters": llama_cpp_parameters,
    "json_to_bbox": json_to_bbox
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "llama_cpp_model_loader": "Llama-cpp Model Loader",
    "llama_cpp_instruct_adv": "Llama-cpp Instruct (Advanced)",
    "llama_cpp_instruct": "Llama-cpp Instruct",
    "llama_cpp_parameters": "Llama-cpp Parameters",
    "json_to_bbox": "JSON to BBOX"
}