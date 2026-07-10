"""
OCR文字识别服务

支持从图片中提取文字信息
用于多模态场景：识别商品参数图、截图、标签等
"""

from typing import List, Dict, Any, Optional, Tuple
from PIL import Image
import io
import logging
import base64

logger = logging.getLogger(__name__)


class OCRResult:
    """OCR识别结果"""

    def __init__(
        self,
        text: str,
        boxes: Optional[List[Dict]] = None,
        confidence: float = 0.0
    ):
        self.text = text  # 完整文本
        self.boxes = boxes or []  # 文字框位置信息
        self.confidence = confidence  # 平均置信度

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "text": self.text,
            "boxes": self.boxes,
            "confidence": self.confidence
        }


class OCRService:
    """
    OCR文字识别服务

    功能：
    1. 识别图片中的中英文文字
    2. 返回文字内容、位置、置信度
    3. 支持Base64图片输入
    """

    def __init__(self, engine: str = "rapidocr"):
        """
        初始化OCR服务

        Args:
            engine: OCR引擎类型 ("rapidocr" 或 "paddleocr")
                    rapidocr: 更快、更轻量
                    paddleocr: 更准确，但更慢
        """
        self.engine = engine
        self._ocr_model = None

        if engine == "rapidocr":
            self._init_rapidocr()
        else:
            self._init_paddleocr()

        logger.info(f"✅ OCR服务初始化成功 (引擎: {engine})")

    def _init_rapidocr(self):
        """初始化RapidOCR引擎（推荐：更快、更轻）"""
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr_model = RapidOCR()
            logger.info("使用RapidOCR引擎")
        except ImportError:
            logger.warning("RapidOCR未安装，回退到PaddleOCR")
            self._init_paddleocr()

    def _init_paddleocr(self):
        """初始化PaddleOCR引擎"""
        try:
            from paddleocr import PaddleOCR
            # PaddleOCR初始化参数：
            # use_angle_cls=True: 启用方向分类器（识别旋转文字）
            # lang="ch": 中英文混合
            # use_gpu=False: CPU模式（GPU需要额外配置）
            # show_log=False: 关闭详细日志
            self._ocr_model = PaddleOCR(
                use_angle_cls=True,
                lang="ch",
                use_gpu=False,
                show_log=False
            )
            self.engine = "paddleocr"
            logger.info("使用PaddleOCR引擎")
        except ImportError:
            logger.error("PaddleOCR未安装，请运行: pip install paddleocr")
            raise RuntimeError("OCR引擎初始化失败")

    async def recognize_image(
        self,
        image_data: bytes,
        return_details: bool = False
    ) -> OCRResult:
        """
        识别图片中的文字

        Args:
            image_data: 图片二进制数据
            return_details: 是否返回详细的位置信息

        Returns:
            OCRResult对象
        """
        try:
            # 将bytes转为PIL Image
            image = Image.open(io.BytesIO(image_data))

            # 转为RGB格式（防止RGBA格式问题）
            if image.mode != 'RGB':
                image = image.convert('RGB')

            # 调用OCR引擎
            if self.engine == "rapidocr":
                return self._recognize_with_rapidocr(image, return_details)
            else:
                return self._recognize_with_paddleocr(image, return_details)

        except Exception as e:
            logger.error(f"OCR识别失败: {e}")
            return OCRResult(text="", confidence=0.0)

    def _recognize_with_rapidocr(
        self,
        image: Image.Image,
        return_details: bool
    ) -> OCRResult:
        """使用RapidOCR识别"""
        import numpy as np

        # PIL转numpy数组
        img_array = np.array(image)

        # RapidOCR返回: (文字列表, 位置列表)
        result, _ = self._ocr_model(img_array)

        if not result:
            return OCRResult(text="", confidence=0.0)

        # 提取文字和位置
        texts = []
        boxes = []
        confidences = []

        for item in result:
            # RapidOCR返回格式: [位置, 文字, 置信度]
            box, text, conf = item
            texts.append(text)
            confidences.append(conf)

            if return_details:
                boxes.append({
                    "box": box,  # 四个角坐标 [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                    "text": text,
                    "confidence": conf
                })

        # 拼接所有文字
        full_text = "\n".join(texts)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return OCRResult(
            text=full_text,
            boxes=boxes if return_details else None,
            confidence=avg_conf
        )

    def _recognize_with_paddleocr(
        self,
        image: Image.Image,
        return_details: bool
    ) -> OCRResult:
        """使用PaddleOCR识别"""
        import numpy as np

        # PIL转numpy数组
        img_array = np.array(image)

        # PaddleOCR返回列表
        result = self._ocr_model.ocr(img_array, cls=True)

        if not result or not result[0]:
            return OCRResult(text="", confidence=0.0)

        # 提取文字和位置
        texts = []
        boxes = []
        confidences = []

        for line in result[0]:
            # PaddleOCR返回格式: [位置, (文字, 置信度)]
            box, (text, conf) = line
            texts.append(text)
            confidences.append(conf)

            if return_details:
                boxes.append({
                    "box": box,  # 四个角坐标
                    "text": text,
                    "confidence": conf
                })

        # 拼接所有文字
        full_text = "\n".join(texts)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return OCRResult(
            text=full_text,
            boxes=boxes if return_details else None,
            confidence=avg_conf
        )

    async def recognize_base64(
        self,
        base64_data: str,
        return_details: bool = False
    ) -> OCRResult:
        """
        识别Base64编码的图片

        Args:
            base64_data: Base64编码的图片数据（可能包含data:image/...前缀）
            return_details: 是否返回详细的位置信息

        Returns:
            OCRResult对象
        """
        try:
            # 移除data:image/...;base64,前缀
            if "," in base64_data:
                base64_data = base64_data.split(",", 1)[1]

            # Base64解码
            image_data = base64.b64decode(base64_data)

            return await self.recognize_image(image_data, return_details)

        except Exception as e:
            logger.error(f"Base64图片OCR识别失败: {e}")
            return OCRResult(text="", confidence=0.0)

    def extract_key_info(self, ocr_text: str) -> Dict[str, Any]:
        """
        从OCR文本中提取关键信息

        智能识别：
        - 价格信息
        - 品牌
        - 型号
        - 参数

        Args:
            ocr_text: OCR识别出的文本

        Returns:
            提取的关键信息字典
        """
        import re

        info = {
            "price": None,
            "brand": None,
            "model": None,
            "category": None,
            "ingredients": [],
            "shade": None,
            "spf": None,
            "pa": None,
            "specs": [],
            "raw_text": ocr_text
        }

        # 提取价格（支持多种格式）
        price_patterns = [
            r'[¥$€£]\s*(\d+(?:\.\d{1,2})?)',  # ¥123.45
            r'(\d+(?:\.\d{1,2})?)\s*[元块钱]',   # 123元
            r'价格[:：]\s*(\d+(?:\.\d{1,2})?)',  # 价格:123
        ]
        for pattern in price_patterns:
            match = re.search(pattern, ocr_text)
            if match:
                try:
                    info["price"] = float(match.group(1))
                    break
                except (ValueError, IndexError):
                    pass

        # 提取常见品牌
        brands = [
            '苹果', 'Apple', '华为', 'Huawei', '小米', 'Xiaomi', '三星', 'Samsung',
            '索尼', 'Sony', '戴森', 'Dyson', 'Nike', 'Adidas', '李宁', '安踏',
            '茅台', '五粮液', '格力', '美的', '海尔', '海信', 'TCL', '联想', 'Lenovo',
            '兰蔻', '雅诗兰黛', 'SK-II', '资生堂', '理肤泉', '薇诺娜', '玉泽', '修丽可',
            '阿玛尼', 'NARS', '植村秀', 'YSL', 'CPB', '迪奥', '魅可', 'MAC', '芭比波朗',
            '花西子', '橘朵', '完美日记', '毛戈平'
        ]
        for brand in brands:
            if brand in ocr_text:
                info["brand"] = brand
                break

        # 提取品类
        category_aliases = {
            "护肤-防晒": ["防晒", "SPF", "PA++", "PA+++"],
            "护肤-卸妆": ["卸妆", "洁肤液", "卸妆油", "卸妆水"],
            "护肤-面霜": ["面霜", "乳霜", "修护霜"],
            "护肤-精华": ["精华", "精华液", "肌底液"],
            "美妆-妆前": ["妆前", "妆前乳", "隔离乳"],
            "美妆-粉底": ["粉底液", "粉底", "持妆粉底"],
            "美妆-气垫": ["气垫", "气垫粉底"],
            "美妆-散粉": ["散粉", "蜜粉", "粉饼", "定妆粉"],
            "美妆-口红": ["口红", "唇釉", "唇泥", "细管"],
            "美妆-腮红": ["腮红", "胭脂"]
        }
        for category, aliases in category_aliases.items():
            if any(alias.lower() in ocr_text.lower() for alias in aliases):
                info["category"] = category
                break

        # 提取型号（常见模式：字母+数字组合）
        model_patterns = [
            r'([A-Z]{2,4}-?\d{3,4})',  # iPhone-15, Mate-60
            r'([A-Z]\d{3,4}[A-Z]?)',    # A15, M2
            r'(\d{3,4}[A-Z])',          # 15Pro
        ]
        for pattern in model_patterns:
            match = re.search(pattern, ocr_text)
            if match:
                info["model"] = match.group(1)
                break

        # 提取色号
        shade_patterns = [
            r'色号[:：]?\s*([A-Za-z]?\d{1,3}[A-Za-z]?|[A-Za-z0-9\-]+)',
            r'(#?[A-Za-z]?\d{1,3})色',
            r'\b([A-Z]\d{1,2})\b'
        ]
        for pattern in shade_patterns:
            match = re.search(pattern, ocr_text, re.IGNORECASE)
            if match:
                info["shade"] = match.group(1)
                break

        # 提取 SPF / PA
        spf_match = re.search(r'SPF\s*([0-9]{2}\+?)', ocr_text, re.IGNORECASE)
        if spf_match:
            info["spf"] = spf_match.group(1)
        pa_match = re.search(r'PA(\+{1,4})', ocr_text, re.IGNORECASE)
        if pa_match:
            info["pa"] = f"PA{pa_match.group(1)}"

        # 提取常见成分
        ingredient_vocab = [
            "烟酰胺", "玻尿酸", "透明质酸", "神经酰胺", "角鲨烷", "积雪草", "二裂酵母",
            "A醇", "视黄醇", "VC", "维C", "壬二酸", "水杨酸", "果酸", "乳糖酸",
            "咖啡因", "肽", "胜肽", "云母", "硅石", "二氧化钛", "氧化锌", "酒精",
            "香精", "矿物油", "甘油", "霍霍巴油"
        ]
        ingredient_hits = []
        lowered_text = ocr_text.lower()
        for ingredient in ingredient_vocab:
            if ingredient.lower() in lowered_text:
                ingredient_hits.append(ingredient)
        info["ingredients"] = ingredient_hits[:8]

        # 提取参数（键值对格式）
        param_patterns = [
            r'([^\s:：]+)[:：]\s*([^\n]+)',  # 键:值
            r'([^\s]+)\s*[：:]\s*([^\n]+)',  # 键：值
        ]
        for pattern in param_patterns:
            matches = re.findall(pattern, ocr_text)
            for key, value in matches:
                if len(key) < 20 and len(value) < 50:  # 过滤过长的匹配
                    info["specs"].append({"key": key.strip(), "value": value.strip()})

        return info


# ==================== 全局实例 ====================

_ocr_service: Optional[OCRService] = None


def get_ocr_service(engine: str = "rapidocr") -> OCRService:
    """
    获取OCR服务实例

    Args:
        engine: OCR引擎类型 ("rapidocr" 或 "paddleocr")

    Returns:
        OCRService单例
    """
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = OCRService(engine=engine)
    return _ocr_service
