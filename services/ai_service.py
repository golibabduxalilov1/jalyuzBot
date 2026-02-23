import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from datetime import datetime, date

from openai import OpenAI  # Yangi SDK

from config import OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger(__name__)

# ==================== AI STATISTICS (for monitoring) ====================
# Maximum number of requests to store in history (to prevent RAM overflow)
MAX_STORED_REQUESTS = 100

# Global counters for AI monitoring
_ai_stats = {
    "request_count_today": 0,
    "last_request_date": None,  # date object
    "last_request_time": None,  # datetime object
    "last_response_time_ms": None,  # milliseconds
    "last_error": None,  # error message
    "last_error_time": None,  # datetime object
    "last_error_type": None,  # error type name
    "api_key_valid": None,  # True/False/None
    "monthly_limit": None,  # monthly limit if available
    "remaining_limit": None,  # remaining limit if available
    # Token va narx monitoring
    "today_ai_requests": 0,
    "today_ai_cost": 0.0,  # dollars
    "month_ai_requests": 0,
    "month_ai_cost": 0.0,  # dollars
    "last_ai_request_time": None,  # datetime object
    "initial_balance": 5.0,  # dollars - admin tomonidan qo'lda belgilanadi
    "last_month_reset_date": None,  # date object - oylik reset
    "request_history": [],  # List of dicts with timestamp, tokens, cost (max 100)
}

# Model narxlari (1000 token uchun dollar)
MODEL_PRICES = {
    "gpt-4o-mini": {
        "input": 0.15 / 1000,  # $0.15 per 1M tokens
        "output": 0.60 / 1000,  # $0.60 per 1M tokens
    },
    "gpt-4o": {
        "input": 2.50 / 1000,  # $2.50 per 1M tokens
        "output": 10.00 / 1000,  # $10.00 per 1M tokens
    },
    "gpt-4": {
        "input": 30.00 / 1000,  # $30.00 per 1M tokens
        "output": 60.00 / 1000,  # $60.00 per 1M tokens
    },
    "gpt-3.5-turbo": {
        "input": 0.50 / 1000,  # $0.50 per 1M tokens
        "output": 1.50 / 1000,  # $1.50 per 1M tokens
    },
}

def _get_model_price(model_name: str) -> dict:
    """Get pricing for a model"""
    model_lower = (model_name or "").lower()
    # Try exact match first
    if model_lower in MODEL_PRICES:
        return MODEL_PRICES[model_lower]
    # Try partial match (e.g., "gpt-4o-mini" matches "gpt-4o-mini")
    for key, price in MODEL_PRICES.items():
        if key in model_lower or model_lower in key:
            return price
    # Default to gpt-4o-mini pricing
    return MODEL_PRICES["gpt-4o-mini"]

def _calculate_cost(tokens_input: int, tokens_output: int, model_name: str) -> float:
    """Calculate cost in dollars based on tokens and model"""
    prices = _get_model_price(model_name)
    input_cost = (tokens_input / 1000) * prices["input"]
    output_cost = (tokens_output / 1000) * prices["output"]
    return input_cost + output_cost

def set_initial_balance(balance: float):
    """Set initial balance (admin function)"""
    _ai_stats["initial_balance"] = balance

def get_initial_balance() -> float:
    """Get initial balance"""
    return _ai_stats.get("initial_balance", 5.0)

def get_ai_stats():
    """Get AI statistics for monitoring"""
    # Reset daily counter if date changed
    today = date.today()
    if _ai_stats["last_request_date"] != today:
        _ai_stats["request_count_today"] = 0
        _ai_stats["today_ai_requests"] = 0
        _ai_stats["today_ai_cost"] = 0.0
        _ai_stats["last_request_date"] = today
    
    # Reset monthly counter if month changed
    current_month = today.replace(day=1)
    last_month_reset = _ai_stats.get("last_month_reset_date")
    if last_month_reset is None or last_month_reset < current_month:
        _ai_stats["month_ai_requests"] = 0
        _ai_stats["month_ai_cost"] = 0.0
        _ai_stats["last_month_reset_date"] = current_month
    
    # Cleanup request history if too large (keep only last 100)
    request_history = _ai_stats.get("request_history", [])
    if len(request_history) > MAX_STORED_REQUESTS:
        _ai_stats["request_history"] = request_history[-MAX_STORED_REQUESTS:]
    
    return _ai_stats.copy()

def _record_ai_request(response_time_ms: float = None, tokens_input: int = 0, tokens_output: int = 0, model_name: str = None):
    """Record AI request for statistics"""
    today = date.today()
    if _ai_stats["last_request_date"] != today:
        _ai_stats["request_count_today"] = 0
        _ai_stats["today_ai_requests"] = 0
        _ai_stats["today_ai_cost"] = 0.0
        _ai_stats["last_request_date"] = today
    
    # Reset monthly counter if month changed
    current_month = today.replace(day=1)
    last_month_reset = _ai_stats.get("last_month_reset_date")
    if last_month_reset is None or last_month_reset < current_month:
        _ai_stats["month_ai_requests"] = 0
        _ai_stats["month_ai_cost"] = 0.0
        _ai_stats["last_month_reset_date"] = current_month
    
    _ai_stats["request_count_today"] += 1
    _ai_stats["today_ai_requests"] += 1
    _ai_stats["last_request_time"] = datetime.now()
    _ai_stats["last_ai_request_time"] = datetime.now()
    
    if response_time_ms is not None:
        _ai_stats["last_response_time_ms"] = response_time_ms
    
    # Calculate and record cost
    cost = 0.0
    if tokens_input > 0 or tokens_output > 0:
        model = model_name or OPENAI_MODEL or "gpt-4o-mini"
        cost = _calculate_cost(tokens_input, tokens_output, model)
        _ai_stats["today_ai_cost"] += cost
        _ai_stats["month_ai_cost"] += cost
        _ai_stats["month_ai_requests"] += 1
    
    # Add to request history (keep only last 100 requests - FIFO)
    request_record = {
        "timestamp": datetime.now().isoformat(),
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "cost": cost,
        "model": model_name or OPENAI_MODEL or "gpt-4o-mini",
        "response_time_ms": response_time_ms,
    }
    
    # Get or initialize request_history
    if "request_history" not in _ai_stats:
        _ai_stats["request_history"] = []
    
    _ai_stats["request_history"].append(request_record)
    
    # Keep only last MAX_STORED_REQUESTS (FIFO)
    if len(_ai_stats["request_history"]) > MAX_STORED_REQUESTS:
        _ai_stats["request_history"] = _ai_stats["request_history"][-MAX_STORED_REQUESTS:]

def _record_ai_error(error: Exception):
    """Record AI error for statistics"""
    _ai_stats["last_error"] = str(error)[:100]
    _ai_stats["last_error_time"] = datetime.now()
    _ai_stats["last_error_type"] = type(error).__name__

# AI Generatsiya uchun USER PROMPT (vizual natija yaratishga yo'naltirilgan)
AI_GENERATION_USER_PROMPT = """Foydalanuvchi yuborgan 1-rasm — xona derazasi.
2-rasm — jalyuzi modeli.

Jalyuzi modelini derazaga O'RNATILGAN HOLATDA tasavvur qilib,
rang uyg'unligi, uslub, proporsiya va amaliy jihatdan tahlil qil.

Faqat vizual natijani va dizayn tavsiyasini yoz."""


@dataclass
class AIResult:
    image_bytes: Optional[bytes]
    description: Optional[str]


TMP_DIR = Path("tmp")


class OpenAIVisionService:
    """Service to interact with OpenAI Responses API for vision generation."""

    def __init__(self) -> None:
        if not OPENAI_API_KEY:
            logger.error("OPENAI_API_KEY is missing. Set it in the .env file.")
            _ai_stats["api_key_valid"] = False
            raise ValueError("OPENAI_API_KEY is required")

        self.model = OPENAI_MODEL or "gpt-4o-mini"
        # Yangi SDK format
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        _ai_stats["api_key_valid"] = True

        logger.info("OpenAI Vision Service initialized with model %s", self.model)

    async def generate_from_images(
        self,
        room_bytes: bytes,
        model_bytes: bytes,
    ) -> AIResult:
        """Generate blended image using OpenAI API with two user-provided images."""
        logger.info("Preparing OpenAI request for AI generation | model=%s", self.model)
        
        # Rasmlar mavjudligini tekshirish
        if not room_bytes or len(room_bytes) == 0:
            logger.error("CRITICAL: room_bytes is empty!")
            raise ValueError("Room image bytes cannot be empty")
        if not model_bytes or len(model_bytes) == 0:
            logger.error("CRITICAL: model_bytes is empty!")
            raise ValueError("Model image bytes cannot be empty")
        
        logger.info("Images received | room_size=%d bytes | model_size=%d bytes", len(room_bytes), len(model_bytes))

        room_b64 = self._encode_image(room_bytes)
        model_b64 = self._encode_image(model_bytes)
        
        # Base64 encoding muvaffaqiyatli bo'lganini tekshirish
        if not room_b64 or len(room_b64) == 0:
            logger.error("CRITICAL: room_b64 encoding failed!")
            raise ValueError("Room image base64 encoding failed")
        if not model_b64 or len(model_b64) == 0:
            logger.error("CRITICAL: model_b64 encoding failed!")
            raise ValueError("Model image base64 encoding failed")
        
        logger.info("Images encoded | room_b64_length=%d | model_b64_length=%d", len(room_b64), len(model_b64))

        TMP_DIR.mkdir(parents=True, exist_ok=True)
        (TMP_DIR / "room.png").write_bytes(room_bytes)
        (TMP_DIR / "model.png").write_bytes(model_bytes)

        start_time = time.time()
        try:
            response = await asyncio.to_thread(
                self._send_generation_request,
                room_b64,
                model_b64,
            )
            # Record successful request
            response_time_ms = (time.time() - start_time) * 1000
            
            # Extract token usage from response
            tokens_input = 0
            tokens_output = 0
            model_used = "gpt-4o-mini"
            
            try:
                if hasattr(response, "usage"):
                    usage = response.usage
                    if hasattr(usage, "prompt_tokens"):
                        tokens_input = usage.prompt_tokens
                    elif isinstance(usage, dict):
                        tokens_input = usage.get("prompt_tokens", 0)
                    
                    if hasattr(usage, "completion_tokens"):
                        tokens_output = usage.completion_tokens
                    elif isinstance(usage, dict):
                        tokens_output = usage.get("completion_tokens", 0)
                
                if hasattr(response, "model"):
                    model_used = response.model
                elif isinstance(response, dict):
                    model_used = response.get("model", "gpt-4o-mini")
            except Exception as e:
                logger.warning(f"Could not extract token usage from response: {e}")
            
            _record_ai_request(response_time_ms, tokens_input, tokens_output, model_used)
            _ai_stats["api_key_valid"] = True
            
            return self._parse_response(response)
        except Exception as exc:
            # Record error
            _record_ai_error(exc)
            _ai_stats["api_key_valid"] = False
            logger.error("OpenAI generation request failed: %s", exc, exc_info=True)
            raise

    def _send_generation_request(self, room_b64: str, model_b64: str):
        """Send generation request to OpenAI with two images."""
        logger.info("Sending AI generation request to OpenAI...")
        
        # Eski OpenAI SDK da rasm bilan ishlash uchun to'g'ri format
        # content ichida list formatida yuborilishi kerak
        # Har bir rasm image_url formatida bo'lishi kerak
        user_content = [
            {
                "type": "text",
                "text": AI_GENERATION_USER_PROMPT
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{room_b64}"
                }
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{model_b64}"
                }
            }
        ]
        
        messages = [
            {
                "role": "user",
                "content": user_content
            }
        ]
        
        # Log: rasm va matn borligini tekshirish
        has_text = any(item.get("type") == "text" for item in user_content)
        has_image = any(item.get("type") == "image_url" for item in user_content)
        image_count = sum(1 for item in user_content if item.get("type") == "image_url")
        logger.info("OpenAI request prepared | has_text=%s | has_image=%s | image_count=%d", has_text, has_image, image_count)
        
        # Rasm yuborilayotganini qo'shimcha tekshirish
        if not has_image:
            logger.error("CRITICAL: No images found in request content!")
            raise ValueError("Images must be included in the request")
        
        if image_count < 2:
            logger.warning("Only %d image(s) found, expected 2", image_count)
        
        try:
            # Yangi SDK format
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",  # Vision qo'llab-quvvatlaydigan model
                messages=messages,
                temperature=0.7,
                max_tokens=1000,
            )
            
            text_content = response.choices[0].message.content if response.choices else "Generatsiya yakunlandi."
            logger.info("OpenAI response received | response_length=%d", len(text_content) if text_content else 0)
            
            # Extract token usage
            tokens_input = 0
            tokens_output = 0
            model_used = "gpt-4o-mini"
            
            if hasattr(response, "usage") and response.usage:
                tokens_input = response.usage.prompt_tokens or 0
                tokens_output = response.usage.completion_tokens or 0
            
            if hasattr(response, "model"):
                model_used = response.model
            
            _record_ai_request(None, tokens_input, tokens_output, model_used)
            _ai_stats["api_key_valid"] = True
            
            # MockResponse formatiga o'tkazish (parse_response uchun)
            class MockResponse:
                def __init__(self, text_content):
                    class MockOutput:
                        def __init__(self, text):
                            class MockContent:
                                def __init__(self, text_val):
                                    self.type = "output_text"
                                    self.text = text_val
                            self.content = [MockContent(text)]
                    self.output = [MockOutput(text_content)]
            
            return MockResponse(text_content)
        except Exception as exc:
            # Record error
            _record_ai_error(exc)
            _ai_stats["api_key_valid"] = False
            logger.error("OpenAI generation request failed: %s | exc_type=%s", exc, type(exc).__name__, exc_info=True)
            raise

    async def generate_from_image_and_text(
        self,
        image_bytes: bytes,
        text_prompt: str,
    ) -> AIResult:
        """Generate result using OpenAI Vision API with one image and text prompt."""
        logger.info("Preparing OpenAI Vision request for AI generation | model=%s", self.model)
        
        # Rasm mavjudligini tekshirish
        if not image_bytes or len(image_bytes) == 0:
            logger.error("CRITICAL: image_bytes is empty!")
            raise ValueError("Image bytes cannot be empty")
        
        if not text_prompt or not text_prompt.strip():
            logger.error("CRITICAL: text_prompt is empty!")
            raise ValueError("Text prompt cannot be empty")
        
        logger.info("Image and text received | image_size=%d bytes | prompt_length=%d", len(image_bytes), len(text_prompt))

        image_b64 = self._encode_image(image_bytes)
        
        # Base64 encoding muvaffaqiyatli bo'lganini tekshirish
        if not image_b64 or len(image_b64) == 0:
            logger.error("CRITICAL: image_b64 encoding failed!")
            raise ValueError("Image base64 encoding failed")
        
        logger.info("Image encoded | image_b64_length=%d", len(image_b64))

        TMP_DIR.mkdir(parents=True, exist_ok=True)
        (TMP_DIR / "ai_gen_image.png").write_bytes(image_bytes)

        start_time = time.time()
        try:
            response = await asyncio.to_thread(
                self._send_vision_request,
                image_b64,
                text_prompt,
            )
            # Record successful request
            response_time_ms = (time.time() - start_time) * 1000
            _record_ai_request(response_time_ms)
            _ai_stats["api_key_valid"] = True
            
            return self._parse_response(response)
        except Exception as exc:
            # Record error
            _record_ai_error(exc)
            _ai_stats["api_key_valid"] = False
            logger.error("OpenAI Vision generation request failed: %s", exc, exc_info=True)
            raise

    def _send_vision_request(self, image_b64: str, text_prompt: str):
        """Send vision request to OpenAI with one image and text prompt."""
        logger.info("Sending AI Vision request to OpenAI...")
        
        # OpenAI Vision API format: image + text together
        user_content = [
            {
                "type": "text",
                "text": text_prompt
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{image_b64}"
                }
            }
        ]
        
        messages = [
            {
                "role": "user",
                "content": user_content
            }
        ]
        
        # Log: rasm va matn borligini tekshirish
        has_text = any(item.get("type") == "text" for item in user_content)
        has_image = any(item.get("type") == "image_url" for item in user_content)
        image_count = sum(1 for item in user_content if item.get("type") == "image_url")
        logger.info("OpenAI Vision request prepared | has_text=%s | has_image=%s | image_count=%d", has_text, has_image, image_count)
        
        # Rasm yuborilayotganini qo'shimcha tekshirish
        if not has_image:
            logger.error("CRITICAL: No images found in Vision request content!")
            raise ValueError("Image must be included in the Vision request")
        
        if not has_text:
            logger.error("CRITICAL: No text prompt found in Vision request content!")
            raise ValueError("Text prompt must be included in the Vision request")
        
        try:
            # Yangi SDK format
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",  # Vision qo'llab-quvvatlaydigan model
                messages=messages,
                temperature=0.7,
                max_tokens=1000,
            )
            
            text_content = response.choices[0].message.content if response.choices else "Generatsiya yakunlandi."
            logger.info("OpenAI Vision response received | response_length=%d", len(text_content) if text_content else 0)
            
            # Extract token usage
            tokens_input = 0
            tokens_output = 0
            model_used = "gpt-4o-mini"
            
            if hasattr(response, "usage") and response.usage:
                tokens_input = response.usage.prompt_tokens or 0
                tokens_output = response.usage.completion_tokens or 0
            
            if hasattr(response, "model"):
                model_used = response.model
            
            _record_ai_request(None, tokens_input, tokens_output, model_used)
            _ai_stats["api_key_valid"] = True
            
            # MockResponse formatiga o'tkazish
            class MockResponse:
                def __init__(self, text_content):
                    class MockOutput:
                        def __init__(self, text):
                            class MockContent:
                                def __init__(self, text_val):
                                    self.type = "output_text"
                                    self.text = text_val
                            self.content = [MockContent(text)]
                    self.output = [MockOutput(text_content)]
            
            return MockResponse(text_content)
        except Exception as exc:
            # Record error
            _record_ai_error(exc)
            _ai_stats["api_key_valid"] = False
            logger.error("OpenAI Vision request failed: %s | exc_type=%s", exc, type(exc).__name__, exc_info=True)
            raise

    async def ai_generate_answer(
        self,
        text: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
    ) -> str:
        """Generate textual answer for general questions."""
        if not text and not image_bytes:
            raise ValueError("Matn yoki rasm bo'lishi shart.")

        messages = []
        if text:
            messages.append({"role": "user", "content": text})
        elif image_bytes:
            image_b64 = self._encode_image(image_bytes)
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Rasm tahlil qilish"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}"
                        }
                    }
                ]
            })

        logger.info("Sending general question to OpenAI | has_text=%s | has_image=%s", bool(text), bool(image_bytes))

        start_time = time.time()
        try:
            # Yangi SDK format
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.4,
            )
            response_time_ms = (time.time() - start_time) * 1000
            
            # Extract token usage
            tokens_input = 0
            tokens_output = 0
            model_used = "gpt-4o-mini"
            
            if hasattr(response, "usage") and response.usage:
                tokens_input = response.usage.prompt_tokens or 0
                tokens_output = response.usage.completion_tokens or 0
            
            if hasattr(response, "model"):
                model_used = response.model
            
            _record_ai_request(response_time_ms, tokens_input, tokens_output, model_used)
            _ai_stats["api_key_valid"] = True
            
            # Extract text from response
            if response.choices and len(response.choices) > 0:
                return response.choices[0].message.content.strip()
            return "Javob topilmadi."
        except Exception as exc:
            # Record error
            _record_ai_error(exc)
            _ai_stats["api_key_valid"] = False
            logger.error("OpenAI QA request failed: %s", exc, exc_info=True)
            raise

    def _parse_response(self, response) -> AIResult:
        logger.info("Parsing OpenAI response payload...")
        image_bytes: Optional[bytes] = None
        description_parts: list[str] = []

        outputs = getattr(response, "output", []) or []
        for output in outputs:
            contents = getattr(output, "content", []) or []
            for content in contents:
                content_type = getattr(content, "type", None)
                if content_type == "output_image":
                    image_obj = getattr(content, "image", None)
                    b64_payload = getattr(image_obj, "base64", None) if image_obj else None
                    if b64_payload:
                        try:
                            image_bytes = base64.b64decode(b64_payload)
                        except Exception as decode_error:
                            logger.error("Failed to decode OpenAI image: %s", decode_error)
                elif content_type == "output_text":
                    text_obj = getattr(content, "text", None)
                    if isinstance(text_obj, str):
                        description_parts.append(text_obj.strip())

        if image_bytes:
            TMP_DIR.mkdir(parents=True, exist_ok=True)
            (TMP_DIR / "result.png").write_bytes(image_bytes)

        description = "\n".join(description_parts).strip() if description_parts else None

        logger.info(
            "OpenAI response parsed | has_image=%s | has_text=%s",
            bool(image_bytes),
            bool(description),
        )

        return AIResult(image_bytes=image_bytes, description=description)

    def _extract_text(self, response) -> str:
        parts: list[str] = []
        outputs = getattr(response, "output", []) or []
        for output in outputs:
            contents = getattr(output, "content", []) or []
            for content in contents:
                if getattr(content, "type", None) == "output_text":
                    text_obj = getattr(content, "text", None)
                    if isinstance(text_obj, str):
                        parts.append(text_obj.strip())

        return "\n\n".join(parts).strip() if parts else "Javob topilmadi."

    def _extract_text_from_chat(self, response) -> str:
        """Extract text from ChatCompletion response (old SDK format)."""
        try:
            if hasattr(response, "choices") and response.choices:
                choice = response.choices[0]
                # Eski SDK da choice dict yoki object bo'lishi mumkin
                if isinstance(choice, dict):
                    message = choice.get("message", {})
                    content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
                else:
                    message = getattr(choice, "message", None)
                    content = getattr(message, "content", "") if message else ""
                
                if content:
                    return content.strip()
        except Exception as exc:
            logger.error("Failed to extract text from ChatCompletion response: %s", exc)
        
        return "Javob topilmadi."

    @staticmethod
    def _encode_image(data: bytes) -> str:
        if not data:
            raise ValueError("Image data is empty.")
        return base64.b64encode(data).decode("utf-8")

    @staticmethod
    def _image_content(b64_data: str) -> dict:
        return {
            "type": "input_image",
            "image_url": f"data:image/png;base64,{b64_data}",
        }


_service_instance: Optional[OpenAIVisionService] = None


def get_openai_service() -> OpenAIVisionService:
    global _service_instance
    if _service_instance is None:
        _service_instance = OpenAIVisionService()
    return _service_instance

