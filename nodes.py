import os
import io
import gc
import json
import base64
import random
import torch

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter

import folder_paths
import comfy.model_management as mm
import comfy.utils

from llama_cpp import Llama
from llama_cpp.llama_chat_format import (
    Llava15ChatHandler, Llava16ChatHandler, MoondreamChatHandler,
    NanoLlavaChatHandler, Llama3VisionAlphaChatHandler, MiniCPMv26ChatHandler,
    Qwen25VLChatHandler, Qwen3VLChatHandler
)

class AnyType(str):
    def __ne__(self, __value: object) -> bool:
        return False

class LLM_STORAGE:
    llm = None
    chat_handler = None
    current_config = None
    
    def clean(self):
        self.llm.close()
        try:
            self.chat_handler._exit_stack.close()
        except Exception:
            pass
        
        self.llm = None
        self.chat_handler = None
        self.current_config = None
        
        gc.collect()
        mm.soft_empty_cache()

any_type = AnyType("*")
model_holder = LLM_STORAGE()

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
    "Creative - Refine & Expand Prompt": "Refine and enhance the following user prompt for creative text-to-@ generation. Keep the meaning and keywords, make it more expressive and visually rich. Output **only the improved prompt text itself**, without any reasoning steps, thinking process, or additional commentary.",
    "Vision - *Bounding Box": 'Locate every instance that belongs to the following categories: "#". Report bbox coordinates in {"bbox_2d": [x1, y1, x2, y2], "label": "string"} JSON format as a List.'
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

def get_chat_handler(chat_handler):
    match chat_handler:
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
            raise ValueError(f'Unknow model type: "{chat_handler}"')

def get_model(config):
    model = config["model"]
    mmproj_model = config["mmproj_model"]
    chat_handler = config["chat_handler"]
    think_mode = config["think_mode"]
    n_ctx = config["n_ctx"]
    n_gpu_layers = config["n_gpu_layers"]
    image_max_tokens = config["image_max_tokens"]
    image_min_tokens = config["image_min_tokens"]
    
    model_path = os.path.join(folder_paths.models_dir, 'LLM', model)
    handler = get_chat_handler(chat_handler)
    if mmproj_model and mmproj_model != "None":
        mmproj_path = os.path.join(folder_paths.models_dir, 'LLM', mmproj_model)
        if chat_handler == "None":
            raise ValueError('"chat_handler" cannot be None!')
        print(f"Loading mmproj from {mmproj_path}")
        if chat_handler == "Qwen3-VL":
            try:
                _chat_handler = handler(
                    clip_model_path=mmproj_path,
                    force_reasoning=think_mode,
                    image_max_tokens=image_max_tokens,
                    image_min_tokens=image_min_tokens,
                    verbose=False)
            except Exception as e:
                if image_max_tokens > 0 or image_min_tokens > 0:
                    raise ValueError('"image_min_tokens" and "image_max_tokens" are unavailable! Please update llama-cpp-python.')
                else:
                    try:
                        _chat_handler = handler(clip_model_path=mmproj_path, force_reasoning=think_mode, verbose=False)
                    except Exception as e:
                        _chat_handler = handler(clip_model_path=mmproj_path, use_think_prompt=think_mode, verbose=False)
        else:
            _chat_handler = handler(clip_model_path=mmproj_path, verbose=False)
    else:
        if handler is not None:
            _chat_handler = handler(verbose=False)
    print(f"Loading model from {model_path}")
    llm = Llama(model_path, chat_handler=_chat_handler, n_gpu_layers=n_gpu_layers, n_ctx=n_ctx, verbose=False)
    return (_chat_handler, llm)

class llama_cpp_model_loader:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "model": (folder_paths.get_filename_list("LLM"), {"tooltip": "Select the model file from the LLM folder."}),
            "mmproj_model": (["None"]+folder_paths.get_filename_list("LLM"), {
                "default": "None",
                "tooltip": "Select the multimodal projector file (mmproj) from the LLM folder."
            }),
            "chat_handler": (["None","Qwen3-VL", "Qwen2.5-VL", "LLaVA-1.5", "LLaVA-1.6", "Moondream2", "nanoLLaVA", "llama3-Vision-Alpha", "MiniCPM-v2.6", "MiniCPM-v4"], {
                "default": "None",
                "tooltip": "Select the chat handler for the model."
            }),
            "think_mode": ("BOOLEAN", {
                "default": False,
                "tooltip": "Enable thinking mode for models that support it."
            }),
            "n_ctx": ("INT", {
                "default": 8192,
                "min": 512, "max": 327680, "step": 128,
                "tooltip": "Context length limit. The maximum number of tokens in the context window."
            }),
            "n_gpu_layers": ("INT", {
                "default": -1,
                "min": -1, "max": 4096, "step": 1,
                "tooltip": "Number of layers to offload to the GPU. Set to -1 to offload all layers."
            }),
            "keep_model_loaded": ("BOOLEAN", {
                "default": True,
                "tooltip": "Whether to keep the model loaded in memory after execution."
            }),
            }
        }

    RETURN_TYPES = ("LLAMACPPMODEL",)
    RETURN_NAMES = ("llamamodel",)
    FUNCTION = "loadmodel"
    CATEGORY = "llama-cpp-vllm"

    def loadmodel(self, model, mmproj_model, chat_handler, think_mode, n_ctx, n_gpu_layers, keep_model_loaded):
        custom_config = {"model": model, "mmproj_model": mmproj_model, "chat_handler":chat_handler, "think_mode": think_mode, "n_ctx": n_ctx, "n_gpu_layers": n_gpu_layers, "keep_model_loaded": keep_model_loaded}
        return (custom_config,)

class llama_cpp_instruct_adv:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "llamamodel": ("LLAMACPPMODEL", {"tooltip": "The loaded Llama model."}),
                "parameters": ("LLAMACPPARAMS", {"tooltip": "Parameters for the Llama model."}),
                "preset_prompt": (preset_tags, {
                    "default": preset_tags[0],
                    "tooltip": "Select a preset prompt."
                }),
                "custom_prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": 'user_prompt\n\nFor preset hints marked with an "*", this will be used to fill the placeholder (e.g., Object names in BBox detection)\nOtherwise, this will override the preset prompts.',
                    "tooltip": "Enter a custom prompt or fill the placeholder in the preset prompt."
                }),
                "system_prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Enter a system prompt to guide the model's behavior."
                }),
                "input_mode": (["one by one", "images", "video"], {
                    "default": "one by one",
                    "tooltip": "one by one: Read one image at a time\n\nimages: Read all images at once\n\nvideo: Treat the input images as video"
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
                "seed": ("INT", {
                    "default": 0, "min": 0, "max": 0xffffffffffffffff, "step": 1,
                    "tooltip": "Seed for random number generation."
                }),
            },
            "optional": {
                "images": ("IMAGE", {"tooltip": "Input images or video frames."}),
            }
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("output",)
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    
    def process(self, llamamodel, parameters, preset_prompt, custom_prompt, system_prompt, input_mode, max_frames, video_size, seed, images=None):
        try:
            mm.soft_empty_cache()
            keep_model_loaded = llamamodel.get('keep_model_loaded', True)
            llamamodel['image_min_tokens'] = parameters.get('image_min_tokens', 0) 
            llamamodel['image_max_tokens'] = parameters.get('image_max_tokens', 0) 
            filtered_params = {k: v for k, v in parameters.items() if k not in {'image_min_tokens', 'image_max_tokens'}}
            video_input = input_mode == "video"
            
            if not model_holder.llm or model_holder.current_config != llamamodel:
                print("[llama-cpp_vllm] Reloading model...")
                if model_holder.llm:
                    model_holder.llm.close()
                    try:
                        model_holder.chat_handler._exit_stack.close()
                    except Exception:
                        pass
                model_holder.current_config = llamamodel.copy()
                model_holder.chat_handler, model_holder.llm = get_model(llamamodel)
            mm.throw_exception_if_processing_interrupted()
            
            messages = []
            
            system_prompts = "请将输入的图片序列当做视频而不是静态帧序列, " + system_prompt if video_input else system_prompt
            if system_prompts.strip():
                messages.append({"role": "system", "content": system_prompts})
                
            user_content = []
            if custom_prompt.strip() and "*" not in preset_prompt:
                user_content.append({"type": "text", "text": custom_prompt})
            else:
                p = preset_prompts[preset_prompt].replace("#", custom_prompt.strip()).replace("@", "video" if video_input else "image")
                user_content.append({"type": "text", "text": p})
                
            if images is not None:
                if not hasattr(model_holder.chat_handler, "clip_model_path") or model_holder.chat_handler.clip_model_path is None:
                     raise ValueError("Image input detected, but the loaded model is not configured with a vision module (mmproj).")
                    
                frames = images
                if video_input:
                    indices = np.linspace(0, len(images) - 1, max_frames, dtype=int)
                    frames = [images[i] for i in indices]
                    
                if input_mode == "one by one":
                    texts = []
                    image_content = {
                        "type": "image_url",
                        "image_url": {"url": ""}
                    }
                    user_content.append(image_content)
                    messages.append({"role": "user", "content": user_content})
                    for i, image in enumerate(frames):
                        mm.throw_exception_if_processing_interrupted()
                        print(f"[llama-cpp_vllm] Reading image {i+1}/{len(frames)}...")
                        data = image2base64(np.clip(255.0 * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8))
                        for item in user_content:
                            if item.get("type") == "image_url":
                                item["image_url"]["url"] = f"data:image/jpeg;base64,{data}"
                                break
                        output = model_holder.llm.create_chat_completion(messages=messages, seed=seed, **filtered_params)
                        text = output['choices'][0]['message']['content']
                        text = text[2:].lstrip() if text.startswith(": ") else text.lstrip() 
                        texts.append(text)
                        
                    if not keep_model_loaded:
                        model_holder.clean()
                    return (texts,)
                else:
                    for image in frames:
                        mm.throw_exception_if_processing_interrupted()
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
                    output = model_holder.llm.create_chat_completion(messages=messages, seed=seed, **filtered_params)
                    text = output['choices'][0]['message']['content']
                    text = text[2:].lstrip() if text.startswith(": ") else text.lstrip() 
            else:
                parameters.pop("image_min_tokens", None)
                parameters.pop("image_max_tokens", None)
                messages.append({"role": "user", "content": user_content})
                output = model_holder.llm.create_chat_completion(messages=messages, seed=seed, **parameters)
                text = output['choices'][0]['message']['content']
                text = text[2:].lstrip() if text.startswith(": ") else text.lstrip() 
                mm.throw_exception_if_processing_interrupted()
            if not keep_model_loaded:
                model_holder.clean()
                
            return (text,)
        except:
            model_holder.clean()
            raise mm.InterruptProcessingException()

class llama_cpp_instruct:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "llamamodel": ("LLAMACPPMODEL", {"tooltip": "The loaded Llama model."}),
                "parameters": ("LLAMACPPARAMS", {"tooltip": "Parameters for the Llama model."}),
                "prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Enter the user prompt."
                }),
                "seed": ("INT", {
                    "default": 0, "min": 0, "max": 0xffffffffffffffff, "step": 1,
                    "tooltip": "Seed for random number generation."
                }),
            },
        }
    
    RETURN_TYPES = ("STRING", )
    RETURN_NAMES = ("output", )
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    
    def process(self, llamamodel, parameters, prompt, seed):
        try:
            mm.soft_empty_cache()
            keep_model_loaded = llamamodel.get('keep_model_loaded', True)
            llamamodel['image_min_tokens'] = parameters.get('image_min_tokens', 0) 
            llamamodel['image_max_tokens'] = parameters.get('image_max_tokens', 0) 
            filtered_params = {k: v for k, v in parameters.items() if k not in {'image_min_tokens', 'image_max_tokens'}}
            
            if not model_holder.llm or model_holder.current_config != llamamodel:
                print("[llama-cpp_vllm] Reloading model...")
                if model_holder.llm:
                    model_holder.llm.close()
                    try:
                        model_holder.chat_handler._exit_stack.close()
                    except Exception:
                        pass
                model_holder.current_config = llamamodel.copy()
                model_holder.chat_handler, model_holder.llm = get_model(llamamodel)
            mm.throw_exception_if_processing_interrupted()
            
            messages = []
            user_content = []
            user_content.append({"type": "text", "text": prompt})
            messages.append({"role": "user", "content": user_content})
            
            output = model_holder.llm.create_chat_completion(messages=messages, seed=seed, **parameters)
            text = output['choices'][0]['message']['content']
            text = text[2:].lstrip() if text.startswith(": ") else text.lstrip() 
            mm.throw_exception_if_processing_interrupted()
            
            if not keep_model_loaded:
                model_holder.clean()
            
            return (text,)
        except:
            model_holder.clean()
            raise mm.InterruptProcessingException()

class llama_cpp_parameters:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
                "max_tokens": ("INT", {"default": 1024, "min": 0, "max": 4096, "step": 1, "tooltip": "Maximum number of tokens to generate."}),
                "top_k": ("INT", {"default": 30, "min": 0, "max": 1000, "step": 1, "tooltip": "Limit the next token selection to the K most likely tokens."}),
                "top_p": ("FLOAT", {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.01, "tooltip": "Limit the next token selection to a subset of tokens with a cumulative probability of P."}),
                "min_p": ("FLOAT", {"default": 0.05, "min": 0.0, "max": 1.0, "step": 0.01, "tooltip": "Minimum probability for a token to be considered."}),
                "typical_p": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01, "tooltip": "Typical probability parameter."}),
                "temperature": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 2.0, "step": 0.01, "tooltip": "Control the randomness of the generation."}),
                "repeat_penalty": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01, "tooltip": "Penalty for repeating tokens."}),
                "frequency_penalty": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.01, "tooltip": "Penalty based on the frequency of tokens in the text so far."}),
                "presence_penalty": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.01, "tooltip": "Penalty based on the presence of tokens in the text so far."}),
                #"tfs_z": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}),
                "mirostat_mode": ("INT", {"default": 0, "min": 0, "max": 2, "step": 1, "tooltip": "Mirostat sampling mode."}),
                "mirostat_eta": ("FLOAT", {"default": 0.1, "min": 0.0, "max": 1.0, "step": 0.01, "tooltip": "Mirostat learning rate (eta)."}),
                "mirostat_tau": ("FLOAT", {"default": 5.0, "min": 0.0, "max": 10.0, "step": 0.01, "tooltip": "Mirostat target entropy (tau)."}),
                "image_min_tokens": ("INT", {"default": 0, "min": 0, "max": 4096, "step": 32, "tooltip": "Minimum number of tokens for image processing."}),
                "image_max_tokens": ("INT", {"default": 0, "min": 0, "max": 4096, "step": 32, "tooltip": "Maximum number of tokens for image processing."}),
                }
        }
    RETURN_TYPES = ("LLAMACPPARAMS",)
    RETURN_NAMES = ("parameters",)
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    def process(self, **kwargs):
        return (kwargs,)
    
class llama_cpp_unload_model:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {"any": (any_type, {"tooltip": "Any input to trigger the unload."})}}
    
    RETURN_TYPES = (any_type,)
    RETURN_NAMES = ("any",)
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    
    def process(self, any):
        model_holder.clean()
        return (any,)

class json_to_bbox:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "json": ("STRING", {"forceInput": True, "tooltip": "Input JSON string containing bounding box information."}),
                "mode": (["simple","Qwen3-VL", "Qwen2-VL"], {
                    "default": "simple",
                    "tooltip": "Mode for drawing bounding boxes."
                }),
                "label": ("STRING", {
                    "default":"",
                    "multiline": False,
                    "tooltip": "Filter bounding boxes by label. Leave empty to select all."
                }),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "Input image to draw bounding boxes on."}),
            }
        }
    
    RETURN_TYPES = ("BBOX", "IMAGE")
    RETURN_NAMES = ("bboxes", "image")
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    
    def process(self, json, mode, label, image=None):
        output = []
        images = []
        for i, j in enumerate(json):
            bboxes = parse_json(j)
            if label != "":
                try:
                    bboxes = [item for item in bboxes if item["label"] == label]
                except Exception:
                    bboxes = [item for item in bboxes if item.get("text_content") == label]
            if image is not None:
                try:
                    images.append(draw_bbox(image[i], bboxes, mode))
                except Exception as e:
                    raise ValueError(f"Unable to draw bbox on image[{i}]!\n{e}")
            if mode in ["Qwen3-VL", "Qwen2-VL"]:
                if image is None:
                    raise ValueError(f'When using the "{mode}" mode, the original input image must be connected!')
                bbox = qwen3bbox(image[i], bboxes)
            else:
                bbox = [tuple(item["bbox_2d"]) for item in bboxes]
            output.append(bbox)
        return(output, torch.cat(images, dim=0),)

class SEG:
    def __init__(self, cropped_image, cropped_mask, confidence, crop_region, bbox, label, control_net_wrapper=None):
        self.cropped_image = cropped_image
        self.cropped_mask = cropped_mask
        self.confidence = confidence
        self.crop_region = crop_region
        self.bbox = bbox
        self.label = label
        self.control_net_wrapper = control_net_wrapper
        
    def __repr__(self):
        return (f"SEG(cropped_image={self.cropped_image}, cropped_mask=shape{self.cropped_mask.shape}, confidence={self.confidence}, bbox={self.bbox}, label='{self.label}'), control_net_wrapper={self.control_net_wrapper}")
    
class bbox_to_segs:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "bboxes": ("BBOX", {"tooltip": "Input bounding boxes."}),
                "image": ("IMAGE", {"tooltip": "Input image for cropping."}),
                "dilation": ("INT", {"default": 10, "min": 0, "max": 200, "step": 1, "tooltip": "Amount to dilate the bounding box."}),
                "feather": ("INT", {"default": 0, "min": 0, "max": 100, "step": 1, "tooltip": "Amount to feather the edges of the mask."}),
            }
        }
    
    RETURN_TYPES = ("SEGS",)
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    
    def process(self, bboxes, image, dilation, feather):
        _batch_size, height, width, _channels = image.shape
        mask_shape = (height, width)
        
        seg_list = []
        image_for_cropping = image[0] 
        
        for bbox in bboxes[0]:
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                print(f"Warning: Skipping invalid bbox item: {bbox}")
                continue
            
            x1, y1, x2, y2 = map(int, bbox)
            x1_exp = x1 - dilation
            y1_exp = y1 - dilation
            x2_exp = x2 + dilation
            y2_exp = y2 + dilation
            
            crop_region = [x1_exp, y1_exp, x2_exp, y2_exp]
            crop_w = x2_exp - x1_exp
            crop_h = y2_exp - y1_exp
            
            if crop_h <= 0 or crop_w <= 0:
                print(f"Warning: Skipping bbox with invalid expanded size: {crop_region}")
                continue
            
            local_mask_np = np.zeros((crop_h, crop_w), dtype=np.float32)
            local_x1 = dilation
            local_y1 = dilation
            local_x2 = local_x1 + (x2 - x1)
            local_y2 = local_y1 + (y2 - y1)
            local_mask_np[local_y1:local_y2, local_x1:local_x2] = 1.0
            
            if feather > 0:
                local_mask_np = gaussian_filter(local_mask_np, sigma=feather)
                
            cropped_mask_np = local_mask_np
            cropped_img_padded = torch.zeros((crop_h, crop_w, 3), dtype=image.dtype, device=image.device)
            
            src_x_start = max(0, x1_exp)
            src_y_start = max(0, y1_exp)
            src_x_end = min(width, x2_exp)
            src_y_end = min(height, y2_exp)
            
            dst_x_start = src_x_start - x1_exp
            dst_y_start = src_y_start - y1_exp
            dst_x_end = src_x_end - x1_exp
            dst_y_end = src_y_end - y1_exp
            
            if src_x_end > src_x_start and src_y_end > src_y_start:
                source_crop = image_for_cropping[src_y_start:src_y_end, src_x_start:src_x_end, :]
                cropped_img_padded[dst_y_start:dst_y_end, dst_x_start:dst_x_end, :] = source_crop
                
            cropped_image_tensor = cropped_img_padded.permute(2, 0, 1).unsqueeze(0)
            
            seg = SEG(
                cropped_image=cropped_image_tensor,
                cropped_mask=cropped_mask_np,
                confidence=np.array([0.9], dtype=np.float32),
                crop_region=crop_region,
                bbox=np.array(bbox, dtype=np.float32),
                label="bbox"
            )
            
            seg_list.append(seg)
            
        segs = (mask_shape, seg_list)
        
        return (segs,)
    
class bbox_to_mask:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "bboxes": ("BBOX", {"tooltip": "Input bounding boxes."}),
                "image": ("IMAGE", {"tooltip": "Input image for mask generation."}),
                "dilation": ("INT", {"default": 10, "min": 0, "max": 200, "step": 1, "tooltip": "Amount to dilate the bounding box."}),
                "feather": ("INT", {"default": 0, "min": 0, "max": 100, "step": 1, "tooltip": "Amount to feather the edges of the mask."}),
            }
        }
    
    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("mask",)
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    
    def process(self, bboxes, image, dilation, feather):
        masks = []
        for i, bbox_list in enumerate(bboxes):
            img = image[i]
            height, width, _channels = img.shape
            mask_shape = (height, width)
            
            combined_full_mask = torch.zeros(mask_shape, dtype=torch.float32, device=img.device)
            
            for bbox in bbox_list:
                if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                    print(f"Warning: Skipping invalid bbox item: {bbox}")
                    continue
                
                x1, y1, x2, y2 = map(int, bbox)
                x1_exp = x1 - dilation
                y1_exp = y1 - dilation
                x2_exp = x2 + dilation
                y2_exp = y2 + dilation
                crop_w = x2_exp - x1_exp
                crop_h = y2_exp - y1_exp
                
                if crop_h <= 0 or crop_w <= 0:
                    continue
                
                local_mask_np = np.zeros((crop_h, crop_w), dtype=np.float32)
                local_x1 = dilation
                local_y1 = dilation
                local_x2 = local_x1 + (x2 - x1)
                local_y2 = local_y1 + (y2 - y1)
                local_mask_np[local_y1:local_y2, local_x1:local_x2] = 1.0
                
                if feather > 0:
                    local_mask_np = gaussian_filter(local_mask_np, sigma=feather)
                    
                current_full_mask_np = np.zeros(mask_shape, dtype=np.float32)
                x1_c, y1_c = max(0, x1_exp), max(0, y1_exp)
                x2_c, y2_c = min(width, x2_exp), min(height, y2_exp)
                
                if x2_c > x1_c and y2_c > y1_c:
                    current_full_mask_np[y1_c:y2_c, x1_c:x2_c] = 1.0
                    
                if feather > 0:
                    current_full_mask_np = gaussian_filter(current_full_mask_np, sigma=feather)
                    
                current_full_mask_tensor = torch.from_numpy(current_full_mask_np).to(img.device)
                combined_full_mask = torch.maximum(combined_full_mask, current_full_mask_tensor)
                
            masks.append(combined_full_mask.unsqueeze(0))
            
        return (torch.cat(masks, dim=0),)

class bboxes_to_bbox:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "bboxes": ("BBOX", {"tooltip": "Input bounding boxes."}),
                "image_index": ("INT", {"default": 0, "min": 0, "max": 1000000, "step": 1, "tooltip": "Index of the image to extract the bbox from."}),
                "bbox_index": ("INT", {
                    "default": 0,
                    "min": -998,
                    "max": 999,
                    "step": 1,
                    "tooltip": "Index of the bounding box to extract. Set to 999 to extract all bounding boxes."
                }),
            }
        }
    
    RETURN_TYPES = ("BBOX",)
    RETURN_NAMES = ("bbox",)
    FUNCTION = "process"
    CATEGORY = "llama-cpp-vllm"
    
    def process(self, bboxes, image_index, bbox_index):
        if bbox_index != 999:
            return ([bboxes[image_index][bbox_index]],)
        return (bboxes[image_index],)
        

NODE_CLASS_MAPPINGS = {
    "llama_cpp_model_loader": llama_cpp_model_loader,
    "llama_cpp_instruct_adv": llama_cpp_instruct_adv,
    "llama_cpp_instruct": llama_cpp_instruct,
    "llama_cpp_parameters": llama_cpp_parameters,
    "llama_cpp_unload_model": llama_cpp_unload_model,
    "json_to_bbox": json_to_bbox,
    "bbox_to_segs": bbox_to_segs,
    "bbox_to_mask": bbox_to_mask,
    "bboxes_to_bbox": bboxes_to_bbox,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "llama_cpp_model_loader": "Llama-cpp Model Loader",
    "llama_cpp_instruct_adv": "Llama-cpp Instruct (Advanced)",
    "llama_cpp_instruct": "Llama-cpp Instruct",
    "llama_cpp_parameters": "Llama-cpp Parameters",
    "llama_cpp_unload_model": "Llama-cpp Unload Model",
    "json_to_bbox": "JSON to BBoxes",
    "bbox_to_segs": "BBoxes to SEGS",
    "bbox_to_mask": "BBoxes to MASK",
    "bboxes_to_bbox": "BBoxes to BBox",
}