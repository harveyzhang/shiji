"""识图：调用 Toklens，把图片变成可见线索和一个搜索词。不识别人物身份。

模型只负责「看见了什么」；看见的东西是否属实，仍由后面的真实搜索决定。
"""
import base64
from io import BytesIO
import os

from PIL import Image, UnidentifiedImageError

from .investigate import GENERIC_NAMES
from .toklens import DEFAULT_MODEL, ToklensChat

__all__ = ['DEFAULT_MODEL', 'ToklensVision', 'default_vision']

KINDS = {'old_photo': '老照片', 'object': '旧物照片'}
SYSTEM = ('你是拾迹的识图助手。只描述图片里看得见的东西：文字、器物的形状和材质、建筑、服饰、场景。'
          '不识别人物身份，不猜人名，不谈政治。看不清就少写，不要编。'
          'term 写这类东西最常用的通用名称，比如「煤油灯」「座钟」「火车站」；图里有看得清的名称文字（招牌、站名、铭文）就优先用它。'
          '照片主体是人时，term 写服饰、器物或场景的名称，比如「清代女子服饰」「二胡」，不要写「老照片」「人物合影」这类泛泛的词。'
          'term 不加材质、颜色、风格和「古董」「复古」「老式」这类修饰（这些写进 observations），也不加「游戏」「截图」「照片」「场景」这类描述词。'
          '只输出 JSON：{"observations": ["不超过 5 条，每条不超过 30 字"], "term": "2 到 8 字的通用名称"}')
NOISE_SUFFIXES = ('游戏场景', '游戏画面', '游戏界面', '截图', '老照片', '照片', '图片')
NOISE_PREFIXES = ('古董', '复古', '仿古', '老式', '旧式')  # 收藏圈的修饰词；资料里多写「煤油灯」而不是「古董煤油灯」
MAX_OBSERVATIONS, OBSERVATION_MAX, TERM_MAX = 5, 30, 12
MAX_SIDE = 1024  # 2.6 MB 的原图单张要 30–44 秒，还会超时；缩到 1024 像素再发


def _mime(image):
    if image.startswith(b'\x89PNG'):
        return 'image/png'
    if image[:4] == b'RIFF' and image[8:12] == b'WEBP':
        return 'image/webp'
    return 'image/jpeg'


def _prepare(image):
    """Shrink to MAX_SIDE on the long edge as JPEG; bytes that are not a readable image are sent as they are."""
    try:
        with Image.open(BytesIO(image)) as picture:
            if picture.format == 'JPEG' and max(picture.size) <= MAX_SIDE:
                return image, 'image/jpeg'
            picture = picture.convert('RGB')
            picture.thumbnail((MAX_SIDE, MAX_SIDE))
            buffer = BytesIO()
            picture.save(buffer, 'JPEG', quality=85)
            return buffer.getvalue(), 'image/jpeg'
    except (UnidentifiedImageError, OSError):
        return image, _mime(image)


def _clean_term(term):
    term = term.strip()
    for suffix in NOISE_SUFFIXES:
        if term.endswith(suffix) and len(term) > len(suffix):
            term = term[:-len(suffix)].strip()
    for prefix in NOISE_PREFIXES:
        if term.startswith(prefix) and len(term) - len(prefix) >= 2:
            term = term[len(prefix):].strip()
    term = term[:TERM_MAX]
    return '' if len(term) < 2 or term in GENERIC_NAMES else term  # 「老照片」去掉「照片」只剩「老」，不能拿去搜


class ToklensVision(ToklensChat):
    label = '识图'

    def __init__(self, key_file=None, *, model=None, **options):
        super().__init__(key_file, model=model or os.environ.get('SHIJI_VISION_MODEL'), **options)

    def __call__(self, image, input_type):
        image, mime = _prepare(image)
        url = f'data:{mime};base64,'+base64.b64encode(image).decode()
        data = self.look(f'这是一张{KINDS.get(input_type, "图片")}。按要求输出 JSON。', url)
        observations = [str(o).strip()[:OBSERVATION_MAX] for o in data.get('observations') or [] if str(o).strip()]
        return {'observations': observations[:MAX_OBSERVATIONS], 'term': _clean_term(str(data.get('term') or ''))}

    def look(self, text, url):
        """Send the prompt and the image; providers with their own vision endpoint override this."""
        return self.ask_json([{'role': 'system', 'content': SYSTEM},
                              {'role': 'user', 'content': [{'type': 'text', 'text': text},
                                                           {'type': 'image_url', 'image_url': {'url': url}}]}])


def default_vision():
    return ToklensVision() if os.environ.get('SHIJI_VISION_KEY_FILE') else None
