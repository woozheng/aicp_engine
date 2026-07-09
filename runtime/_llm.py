# aicp/llm.py

import asyncio
import json
import time
from typing import Dict, List, Optional, AsyncIterator, Union
from openai import AsyncOpenAI, AsyncStream
import httpx

DEBUG = True


def _log(msg):
    if DEBUG:
        print(f"[LLM] {msg}")


class LLM:
    def __init__(self, config: dict):
        self._roles = config.get("models", {}).get("roles", {})
        self.default_model = self._roles.get("default", config.get("models", {}).get("default", "gpt-3.5-turbo"))
        
        self.providers = config.get("models", {}).get("providers", {})
        self._clients = {}
        self._model_to_client = {}
        self._model_configs = {}
        self.high_quality_model = None
        self.max_retries = config.get("models", {}).get("max_retries", 3)
        self.request_timeout = config.get("models", {}).get("request_timeout", 60)
        _log(f"config models keys: {list(config.get('models', {}).keys())}")
        _log(f"request_timeout: {config.get('models', {}).get('request_timeout', 'NOT FOUND')}")
        self.stream_timeout = config.get("models", {}).get("stream_timeout", 300)
        self.connect_timeout = config.get("models", {}).get("connect_timeout", 10)
        self._semaphore = asyncio.Semaphore(config.get("models", {}).get("max_concurrent", 10))

        self._nebula_name = None
        self._nebula_base_url = None
        self._fallback_model = None

        self._init_clients()

    def _init_clients(self):
        for name, cfg in self.providers.items():
            api_key = cfg.get("api_key", "")
            base_url = cfg.get("base_url", "https://api.openai.com/v1")

            # ============================================================
            # Nebula 集群 Provider
            # ============================================================
            if cfg.get("type") == "cluster":
                self._clients[name] = {"type": "cluster", "base_url": base_url}
                if not self._nebula_name:
                    self._nebula_name = name
                    self._nebula_base_url = base_url
                for model_cfg in cfg.get("models", []):
                    model_id = model_cfg.get("id")
                    self._model_configs[model_id] = {
                        "client": name,
                        "max_tokens": model_cfg.get("max_tokens", 4096),
                        "temperature": model_cfg.get("temperature", 0.7),
                        "supports_streaming": False,
                    }
                    self._model_to_client[model_id] = name
                    if model_cfg.get("high_quality"):
                        self.high_quality_model = model_id
                    _log(f"✅ {model_id} ({name}) [cluster]")
                continue

            # ============================================================
            # OpenAI 兼容 Provider
            # ============================================================
            if not api_key or api_key.startswith("${"):
                _log(f"⚠️ Skip {name}: API key not configured")
                continue

            timeout = httpx.Timeout(
                timeout=120.0,
                connect=self.connect_timeout,
                read=90.0,
                write=30.0,
                pool=self.connect_timeout
            )
            
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                http_client=httpx.AsyncClient(
                    timeout=timeout,
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
                ),
                max_retries=0
            )
            self._clients[name] = client

            for model_cfg in cfg.get("models", []):
                model_id = model_cfg.get("id")
                self._model_configs[model_id] = {
                    "client": name,
                    "max_tokens": model_cfg.get("max_tokens", 4096),
                    "temperature": model_cfg.get("temperature", 0.7),
                    "supports_streaming": model_cfg.get("supports_streaming", True),
                }
                self._model_to_client[model_id] = name
                if model_cfg.get("high_quality"):
                    self.high_quality_model = model_id
                _log(f"✅ {model_id} ({name})")

        # 确保 default_model 存在
        if self.default_model not in self._model_to_client:
            if self._model_to_client:
                self.default_model = next(iter(self._model_to_client))
                _log(f"⚠️ roles.default 模型不可用，改用: {self.default_model}")
            else:
                _log(f"❌ 没有任何可用模型！")

        # 找第一个非 nebula 模型作为降级
        for model_id, client_name in self._model_to_client.items():
            client = self._clients.get(client_name)
            is_nebula = False
            if isinstance(client, dict):
                is_nebula = client.get("type") == "cluster"
            if not is_nebula and not client_name.startswith("nebula"):
                self._fallback_model = model_id
                break

        _log(f"🤖 Default: {self.default_model} | Models: {len(self._model_to_client)}")
        if self._roles:
            _log(f"📋 Roles: {self._roles}")

    # ============================================================
    # 模型解析
    # ============================================================
    def _resolve_model(self, role: str = None) -> str:
        if role:
            model = self._roles.get(role)
            if model and model in self._model_to_client:
                return model
            elif model:
                _log(f"⚠️ roles.{role}={model} 不可用，降级到 default")
        return self.default_model

    def _get_client(self, model: str = None):
        model = model or self.default_model
        client_name = self._model_to_client.get(model)
        if client_name:
            return self._clients[client_name], model
        if self._clients:
            name = next(iter(self._clients))
            return self._clients[name], model
        return None, model

    def _has_image_or_file(self, messages: List[Dict]) -> bool:
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if part.get("type") in ("image_url", "file"):
                        return True
        return False

    # ============================================================
    # Nebula 集群调用
    # ============================================================
    def _call_nebula_sync(self, base_url: str, messages: List[Dict]) -> Optional[str]:
        """同步调用 Newbula Plus（支持纯文字、图片、文件）"""
        try:
            import requests

            image_path = None
            file_path = None
            text_messages = []

            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if part.get("type") == "image_url":
                            url = part.get("image_url", {}).get("url", "")
                            if url.startswith("file://"):
                                image_path = url[7:]
                            else:
                                image_path = url
                        elif part.get("type") == "file":
                            file_path = part.get("file", {}).get("file_path", "")
                        else:
                            text_parts.append(part.get("text", ""))
                    msg = {"role": msg.get("role", "user"), "content": " ".join(text_parts)}
                text_messages.append(msg)

            if file_path:
                action = "chat_with_file"
                payload = {"action": action, "messages": text_messages, "file_path": file_path}
            elif image_path:
                action = "chat_with_image"
                payload = {"action": action, "messages": text_messages, "image_path": image_path}
            else:
                action = "chat"
                payload = {"action": action, "messages": text_messages}

            t0 = time.time()
            resp = requests.post(
                base_url,
                json={"payload": payload},
                timeout=(10, 180)
            )
            
            data = resp.json()
            
            if data.get("error"):
                _log(f"nebula error: {data.get('error')}")
                return None
            
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if content and content.strip():
                    _log(f"nebula ← {len(content)} chars, {time.time()-t0:.1f}s")
                    return content
            
            _log(f"nebula ← 空 ({time.time()-t0:.1f}s)")
            return None

        except requests.exceptions.ConnectionError:
            _log("nebula connection error")
            return None
        except Exception as e:
            _log(f"nebula error: {type(e).__name__}: {str(e)[:100]}")
            return None

    def _generate_image_nebula_sync(self, base_url: str, prompt: str, n: int = 4) -> List[str]:
        """同步调用 Newbula Plus 生图"""
        try:
            import requests

            payload = {"action": "image", "prompt": prompt, "n": n}

            resp = requests.post(
                base_url,
                json={"payload": payload},
                timeout=(10, 180)
            )
            
            data = resp.json()
            
            if data.get("error"):
                _log(f"nebula image error: {data.get('error')}")
                return []
            
            images = data.get("data", [])
            urls = [img.get("url") for img in images if img.get("url")]
            _log(f"nebula image ← {len(urls)} 张")
            return urls

        except Exception as e:
            _log(f"nebula image error: {e}")
            return []

    # ============================================================
    # 核心实现
    # ============================================================
    async def _chat_impl(self, messages, model=None, **kwargs):
        """内部实现，确保不返回 None"""
        client, actual_model = self._get_client(model)
        result = "[LLM 未配置]"

        if not client:
            return result

        # ============================================================
        # Nebula 集群
        # ============================================================
        if isinstance(client, dict) and client.get("type") == "cluster":
            _log(f"nebula cluster path, model={actual_model}")
            try:
                loop = asyncio.get_running_loop()
                raw_result = await asyncio.wait_for(
                    loop.run_in_executor(None, self._call_nebula_sync, client["base_url"], messages),
                    timeout=180
                )
                
                if raw_result is None:
                    result = None
                else:
                    result = raw_result.strip() if isinstance(raw_result, str) else str(raw_result)
                    if not result:
                        result = None
                        
            except asyncio.TimeoutError:
                _log("nebula timeout")
                result = None
            except Exception as e:
                _log(f"nebula error: {type(e).__name__}: {str(e)[:100]}")
                result = None

            # Nebula 失败 → 降级
            if result is None and self._fallback_model:
                _log(f"🔄 Nebula 不可用，降级到 {self._fallback_model}")
                return await self._chat_impl(messages, self._fallback_model, **kwargs)
            
            return result if result else "[Nebula 不可用且无降级模型]"

        # ============================================================
        # default 模型先偷跑 Nebula
        # ============================================================
        if actual_model == self.default_model and self._nebula_name:
            nebula_client = self._clients.get(self._nebula_name)
            if nebula_client and isinstance(nebula_client, dict) and nebula_client.get("type") == "cluster":
                try:
                    loop = asyncio.get_running_loop()
                    raw = await loop.run_in_executor(None, self._call_nebula_sync, nebula_client["base_url"], messages)
                    if raw and raw.strip() and not raw.startswith("["):
                        _log(f"✅ Nebula 命中！")
                        return raw
                except:
                    pass

        # ============================================================
        # OpenAI 兼容客户端
        # ============================================================
        config = self._model_configs.get(actual_model, {})
        max_tokens = kwargs.pop("max_tokens", config.get("max_tokens", 4096))
        temperature = kwargs.pop("temperature", config.get("temperature", 0.7))

        for attempt in range(self.max_retries):
            try:
                _log(f"🔄 API call {attempt+1}/{self.max_retries} → {actual_model}")
                t0 = time.time()
                
                task = asyncio.create_task(
                    client.chat.completions.create(
                        model=actual_model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        **kwargs
                    )
                )
                
                try:
                    response = await asyncio.wait_for(task, timeout=self.request_timeout)
                except asyncio.TimeoutError:
                    task.cancel()
                    try:
                        await task
                    except:
                        pass
                    raise
                
                elapsed = time.time() - t0
                
                if response and response.choices and response.choices[0].message.content:
                    result = response.choices[0].message.content.strip()
                    if result:
                        _log(f"✅ 成功 ({elapsed:.1f}s, {len(result)} chars)")
                        return result
                
                _log(f"⚠️ 空响应 ({elapsed:.1f}s)")
                if attempt == self.max_retries - 1:
                    return "[模型返回空响应，请稍后重试]"
                    
            except asyncio.TimeoutError:
                _log(f"⏱️ 超时 attempt {attempt+1}")
                if attempt == self.max_retries - 1:
                    return "[服务请求超时，请稍后重试]"
            except Exception as e:
                error_msg = str(e)
                _log(f"❌ 错误 attempt {attempt+1}: {type(e).__name__}: {error_msg[:100]}")
                if attempt == self.max_retries - 1:
                    return f"[LLM请求失败: {error_msg[:200]}]"
            
            if attempt < self.max_retries - 1:
                wait_time = min(2 ** attempt, 10)
                await asyncio.sleep(wait_time)

        return "[LLM 达到最大重试次数]"

    # ============================================================
    # 公开接口
    # ============================================================

    async def chat(self, messages: List[Dict], model: str = None, role: str = None, **kwargs) -> str:
        """
        聊天接口
        
        Args:
            messages: 消息列表
            model: 直接指定模型名（优先级最高）
            role: 通过角色选择模型（"default", "coding", "reasoning", "image"）
        """
        if model:
            resolved_model = model
        elif role:
            resolved_model = self._resolve_model(role)
        else:
            resolved_model = self.default_model
        
        _log(f"💬 chat: {resolved_model}" + (f" (role={role})" if role else ""))
        
        try:
            async with self._semaphore:
                result = await self._chat_impl(messages, resolved_model, **kwargs)
                
                if result is None:
                    result = "[系统错误]"
                elif not isinstance(result, str):
                    result = str(result)
                elif not result.strip():
                    result = "[空响应]"
                    
                return result
        except Exception as e:
            _log(f"❌ chat 异常: {type(e).__name__}: {str(e)[:100]}")
            return f"[系统错误: {str(e)[:100]}]"

    async def _chat_stream_impl(self, messages, model=None, **kwargs):
        """流式实现"""
        client, actual_model = self._get_client(model)

        if not client:
            yield "[LLM not configured]"
            return

        # ============================================================
        # Nebula 集群流式（模拟）
        # ============================================================
        if isinstance(client, dict) and client.get("type") == "cluster":
            result = await self._chat_impl(messages, model, **kwargs)
            if result:
                chunk_size = 10
                for i in range(0, len(result), chunk_size):
                    yield result[i:i+chunk_size]
                    await asyncio.sleep(0.01)
            else:
                yield "[空响应]"
            return

        # ============================================================
        # default 模型先偷跑 Nebula
        # ============================================================
        if actual_model == self.default_model and self._nebula_name:
            nebula_client = self._clients.get(self._nebula_name)
            if nebula_client and isinstance(nebula_client, dict) and nebula_client.get("type") == "cluster":
                try:
                    loop = asyncio.get_running_loop()
                    raw = await loop.run_in_executor(None, self._call_nebula_sync, nebula_client["base_url"], messages)
                    if raw and raw.strip() and not raw.startswith("["):
                        _log(f"✅ Nebula 流式命中！")
                        chunk_size = 10
                        for i in range(0, len(raw), chunk_size):
                            yield raw[i:i+chunk_size]
                            await asyncio.sleep(0.01)
                        return
                except:
                    pass

        # ============================================================
        # OpenAI 兼容客户端
        # ============================================================
        config = self._model_configs.get(actual_model, {})
        if not config.get("supports_streaming", True):
            result = await self._chat_impl(messages, model, **kwargs)
            if result:
                yield result
            else:
                yield "[空响应]"
            return

        max_tokens = kwargs.pop("max_tokens", config.get("max_tokens", 4096))
        temperature = kwargs.pop("temperature", config.get("temperature", 0.7))

        for attempt in range(self.max_retries):
            stream = None
            try:
                _log(f"📡 stream {attempt+1}/{self.max_retries} → {actual_model}")
                stream = await client.chat.completions.create(
                    model=actual_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                    **kwargs
                )
                collected = []
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        if token:
                            collected.append(token)
                            yield token
                _log(f"✅ stream 完成: {len(''.join(collected))} chars")
                return
            except asyncio.TimeoutError:
                _log(f"⏱️ stream 超时 attempt {attempt+1}")
                if attempt == self.max_retries - 1:
                    yield "[LLM stream timeout]"
                    return
            except Exception as e:
                error_msg = str(e)
                _log(f"❌ stream 错误: {type(e).__name__}: {error_msg[:100]}")
                if attempt == self.max_retries - 1:
                    yield f"[LLM stream error: {error_msg[:200]}]"
                    return
            finally:
                if stream and hasattr(stream, 'close'):
                    try:
                        await stream.close()
                    except:
                        pass
            
            if attempt < self.max_retries - 1:
                await asyncio.sleep(min(2 ** attempt, 10))

        yield "[LLM max retries exceeded]"

    async def chat_stream(self, messages, model=None, role=None, **kwargs):
        """
        流式聊天接口
        
        Args:
            messages: 消息列表
            model: 直接指定模型名（优先级最高）
            role: 通过角色选择模型
        """
        if model:
            resolved_model = model
        elif role:
            resolved_model = self._resolve_model(role)
        else:
            resolved_model = self.default_model
        
        async with self._semaphore:
            async for token in self._chat_stream_impl(messages, resolved_model, **kwargs):
                if token:
                    yield token

    # ============================================================
    # 生图接口
    # ============================================================
    async def generate_image(self, prompt: str, model: str = None, n: int = 4, **kwargs) -> List[str]:
        """
        图像生成接口
        
        Args:
            prompt: 图片描述
            model: 模型名
            n: 生成数量
        """
        if model:
            resolved_model = model
        else:
            resolved_model = self.default_model
        
        client, actual_model = self._get_client(resolved_model)
        
        if isinstance(client, dict) and client.get("type") == "cluster":
            _log(f"🎨 generate_image: nebula cluster, prompt={prompt[:50]}...")
            loop = asyncio.get_running_loop()
            urls = await loop.run_in_executor(
                None, self._generate_image_nebula_sync,
                client["base_url"], prompt, n
            )
            return urls
        
        _log(f"⚠️ generate_image: {actual_model} 不支持生图")
        return []

    # ============================================================
    # JSON 接口
    # ============================================================
    async def chat_json(self, messages, model=None, role=None, **kwargs):
        """
        JSON 格式聊天接口
        
        Args:
            messages: 消息列表
            model: 直接指定模型名（优先级最高）
            role: 通过角色选择模型
        """
        raw = "[空响应]"
        try:
            for attempt in range(3):
                raw = await self.chat(messages, model=model, role=role, **kwargs)
                if not raw or raw.startswith("["):
                    if attempt == 2:
                        return {"error": raw}
                    continue
                    
                cleaned = raw.strip()
                if "```json" in cleaned:
                    cleaned = cleaned.split("```json")[1].split("```")[0].strip()
                elif "```" in cleaned:
                    parts = cleaned.split("```")
                    if len(parts) > 1:
                        cleaned = parts[1].split("```")[0].strip()
                        
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError as e:
                    _log(f"JSON 解析失败 {attempt+1}: {e}")
                    if attempt == 2:
                        return {"content": cleaned, "parse_error": str(e)}
                    messages.append({
                        "role": "system",
                        "content": "请只返回有效的 JSON 格式，不要包含任何其他文本。"
                    })
                    
            return {"content": raw}
        except Exception as e:
            _log(f"chat_json 异常: {e}")
            return {"error": str(e), "content": str(raw)}

    # ============================================================
    # 健康检查
    # ============================================================
    async def health_check(self) -> bool:
        """健康检查"""
        for name, client in self._clients.items():
            try:
                if isinstance(client, dict) and client.get("type") == "cluster":
                    continue
                await asyncio.wait_for(client.models.list(), timeout=5)
                return True
            except:
                continue
        return False