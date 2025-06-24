from flask import Flask, jsonify, request
from flask_cors import CORS
import yt_dlp
import json
import re
from urllib.parse import urlparse
import logging
from datetime import datetime
import os

# إعداد التطبيق
app = Flask(__name__)
CORS(app)

# إعداد نظام السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VideoAnalyzer:
    """محرك تحليل الفيديوهات المتقدم"""

    def __init__(self):
        # إعدادات yt-dlp المحسنة للأداء والموثوقية
        self.ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'format': 'best',
            'writethumbnail': False,
            'writeinfojson': False,
            'extract_comments': False,
            'extract_auto_captions': False,
            'socket_timeout': 30,
            'retries': 3,
        }

    def validate_url(self, url):
        """التحقق من صحة الرابط وتنظيفه"""
        if not url or not isinstance(url, str):
            return False, "يرجى إدخال رابط صحيح"

        # تنظيف الرابط من المسافات والأحرف الزائدة
        url = url.strip()

        # التحقق من وجود بروتوكول
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        # التحقق من صحة الرابط باستخدام regex
        url_pattern = re.compile(
            r'^https?://'  # http:// أو https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
            r'localhost|'  # localhost
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
            r'(?::\d+)?'  # port اختياري
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)

        if not url_pattern.match(url):
            return False, "صيغة الرابط غير صحيحة"

        return True, url

    def format_duration(self, duration):
        """تحويل مدة الفيديو إلى تنسيق مقروء"""
        if not duration:
            return "غير محدد"

        try:
            duration = int(duration)
            hours = duration // 3600
            minutes = (duration % 3600) // 60
            seconds = duration % 60

            if hours > 0:
                return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                return f"{minutes:02d}:{seconds:02d}"
        except:
            return "غير محدد"

    def format_filesize(self, filesize):
        """تحويل حجم الملف إلى تنسيق مقروء"""
        if not filesize:
            return "غير محدد"

        try:
            filesize = int(filesize)
            for unit in ['B', 'KB', 'MB', 'GB']:
                if filesize < 1024.0:
                    return f"{filesize:.1f} {unit}"
                filesize /= 1024.0
            return f"{filesize:.1f} TB"
        except:
            return "غير محدد"

    def categorize_formats(self, formats):
        """تصنيف الصيغ بذكاء إلى الفئات المطلوبة"""
        combined = []  # فيديو + صوت
        video_only = []  # فيديو فقط
        audio_only = []  # صوت فقط

        for fmt in formats:
            if not fmt.get('url'):
                continue

            format_info = {
                'id': fmt.get('format_id', ''),
                'url': fmt.get('url', ''),
                'ext': fmt.get('ext', 'mp4'),
                'quality': fmt.get('format_note', 'جودة عادية'),
                'filesize': self.format_filesize(fmt.get('filesize')),
                'filesize_bytes': fmt.get('filesize', 0),
                'width': fmt.get('width'),
                'height': fmt.get('height'),
                'fps': fmt.get('fps'),
                'vcodec': fmt.get('vcodec', 'none'),
                'acodec': fmt.get('acodec', 'none'),
                'abr': fmt.get('abr'),  # audio bitrate
                'tbr': fmt.get('tbr'),  # total bitrate
            }

            # تصنيف ذكي للصيغ
            has_video = fmt.get('vcodec') not in ['none', None] and fmt.get('width')
            has_audio = fmt.get('acodec') not in ['none', None]

            if has_video and has_audio:
                # فيديو + صوت (الأولوية للجودات الشائعة)
                if fmt.get('height'):
                    quality_label = f"{fmt.get('height')}p"
                    if fmt.get('fps') and fmt.get('fps') > 30:
                        quality_label += f"{fmt.get('fps')}"
                    format_info['quality'] = quality_label
                combined.append(format_info)

            elif has_video and not has_audio:
                # فيديو فقط (للمحترفين)
                if fmt.get('height'):
                    quality_label = f"{fmt.get('height')}p (فيديو فقط)"
                    format_info['quality'] = quality_label
                video_only.append(format_info)

            elif has_audio and not has_video:
                # صوت فقط
                if fmt.get('abr'):
                    quality_label = f"{fmt.get('abr')}kbps"
                elif fmt.get('ext') in ['mp3', 'm4a', 'webm']:
                    quality_label = f"{fmt.get('ext').upper()}"
                else:
                    quality_label = "جودة عادية"
                format_info['quality'] = quality_label
                audio_only.append(format_info)

        # ترتيب الصيغ حسب الجودة والحجم
        combined.sort(key=lambda x: (x.get('height', 0), x.get('filesize_bytes', 0)), reverse=True)
        video_only.sort(key=lambda x: (x.get('height', 0), x.get('filesize_bytes', 0)), reverse=True)
        audio_only.sort(key=lambda x: (x.get('abr', 0), x.get('filesize_bytes', 0)), reverse=True)

        return {
            'combined': combined[:10],  # أفضل 10 صيغ مدمجة
            'video_only': video_only[:5],  # أفضل 5 صيغ فيديو فقط
            'audio_only': audio_only[:5]  # أفضل 5 صيغ صوت فقط
        }

    def extract_video_info(self, url):
        """استخراج معلومات الفيديو الكاملة"""
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                logger.info(f"بدء تحليل الرابط: {url}")

                # استخراج المعلومات
                info = ydl.extract_info(url, download=False)

                # استخراج الصورة المصغرة (أفضل جودة متاحة)
                thumbnail = None
                if info.get('thumbnails'):
                    # اختيار أفضل صورة مصغرة
                    thumbnails = sorted(info['thumbnails'], 
                                      key=lambda x: (x.get('width', 0) * x.get('height', 0)), 
                                      reverse=True)
                    thumbnail = thumbnails[0].get('url') if thumbnails else None
                elif info.get('thumbnail'):
                    thumbnail = info['thumbnail']

                # تصنيف الصيغ
                formats_categorized = self.categorize_formats(info.get('formats', []))

                # بناء الاستجابة النهائية
                result = {
                    'success': True,
                    'video_info': {
                        'title': info.get('title', 'عنوان غير محدد'),
                        'description': info.get('description', '')[:500] if info.get('description') else '',
                        'thumbnail': thumbnail,
                        'duration': self.format_duration(info.get('duration')),
                        'duration_seconds': info.get('duration', 0),
                        'uploader': info.get('uploader', 'غير محدد'),
                        'uploader_id': info.get('uploader_id', ''),
                        'view_count': info.get('view_count', 0),
                        'upload_date': info.get('upload_date', ''),
                        'webpage_url': info.get('webpage_url', url),
                        'extractor': info.get('extractor', ''),
                        'platform': self.detect_platform(url)
                    },
                    'formats': formats_categorized,
                    'total_formats': {
                        'combined': len(formats_categorized['combined']),
                        'video_only': len(formats_categorized['video_only']),
                        'audio_only': len(formats_categorized['audio_only'])
                    },
                    'extracted_at': datetime.now().isoformat(),
                    'original_url': url
                }

                logger.info(f"تم تحليل الفيديو بنجاح: {info.get('title', 'بدون عنوان')}")
                return result

        except yt_dlp.DownloadError as e:
            error_msg = str(e)
            if 'Video unavailable' in error_msg:
                return self.create_error_response("الفيديو غير متاح أو محمي", "VIDEO_UNAVAILABLE")
            elif 'Private video' in error_msg:
                return self.create_error_response("الفيديو خاص ولا يمكن الوصول إليه", "PRIVATE_VIDEO")
            elif 'not supported' in error_msg.lower():
                return self.create_error_response("المنصة غير مدعومة حالياً", "UNSUPPORTED_PLATFORM")
            else:
                return self.create_error_response(f"خطأ في تحليل الفيديو: {error_msg}", "EXTRACTION_ERROR")

        except Exception as e:
            logger.error(f"خطأ غير متوقع: {str(e)}")
            return self.create_error_response("حدث خطأ غير متوقع، يرجى المحاولة مرة أخرى", "UNEXPECTED_ERROR")

    def detect_platform(self, url):
        """تحديد المنصة من الرابط"""
        domain = urlparse(url).netloc.lower()

        if 'youtube.com' in domain or 'youtu.be' in domain:
            return 'YouTube'
        elif 'facebook.com' in domain or 'fb.watch' in domain:
            return 'Facebook'
        elif 'tiktok.com' in domain:
            return 'TikTok'
        elif 'instagram.com' in domain:
            return 'Instagram'
        elif 'twitter.com' in domain or 'x.com' in domain:
            return 'Twitter/X'
        elif 'vimeo.com' in domain:
            return 'Vimeo'
        elif 'dailymotion.com' in domain:
            return 'Dailymotion'
        else:
            return 'أخرى'

    def create_error_response(self, message, error_code):
        """إنشاء استجابة خطأ منظمة"""
        return {
            'success': False,
            'error': {
                'message': message,
                'code': error_code,
                'timestamp': datetime.now().isoformat()
            }
        }

# إنشاء محلل الفيديوهات
analyzer = VideoAnalyzer()

@app.route('/')
def home():
    """الصفحة الرئيسية - معلومات عن الـ API"""
    return jsonify({
        'service': 'محرك تحميل الفيديوهات المتقدم',
        'version': '1.0.0',
        'status': 'متاح',
        'endpoints': {
            '/api/fetch': 'تحليل واستخراج معلومات الفيديو',
            '/api/health': 'فحص حالة الخدمة'
        },
        'supported_platforms': [
            'YouTube', 'Facebook', 'TikTok', 'Instagram', 
            'Twitter/X', 'Vimeo', 'Dailymotion', 'وأكثر من 1000 موقع'
        ],
        'features': [
            'استخراج معلومات الفيديو الكاملة',
            'تصنيف ذكي للصيغ المتاحة',
            'دعم جميع الجودات والصيغ',
            'معالجة متقدمة للأخطاء',
            'واجهة برمجية سهلة الاستخدام'
        ]
    })

@app.route('/api/health')
def health_check():
    """فحص حالة الخدمة"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'video-downloader-api'
    })

@app.route('/api/fetch', methods=['POST', 'GET'])
def fetch_video():
    """النقطة الرئيسية لتحليل الفيديوهات"""

    # التعامل مع الطلبات من نوع GET و POST
    if request.method == 'POST':
        data = request.get_json() or {}
        url = data.get('url') or request.form.get('url')
    else:  # GET
        url = request.args.get('url')

    # التحقق من وجود الرابط
    if not url:
        return jsonify({
            'success': False,
            'error': {
                'message': 'يرجى تقديم رابط الفيديو',
                'code': 'MISSING_URL',
                'timestamp': datetime.now().isoformat()
            }
        }), 400

    # التحقق من صحة الرابط
    is_valid, processed_url = analyzer.validate_url(url)
    if not is_valid:
        return jsonify({
            'success': False,
            'error': {
                'message': processed_url,  # رسالة الخطأ
                'code': 'INVALID_URL',
                'timestamp': datetime.now().isoformat()
            }
        }), 400

    # تحليل الفيديو
    result = analyzer.extract_video_info(processed_url)

    # إرجاع النتيجة مع الكود المناسب
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400

@app.errorhandler(404)
def not_found(error):
    """معالج خطأ 404"""
    return jsonify({
        'success': False,
        'error': {
            'message': 'نقطة الوصول غير موجودة',
            'code': 'NOT_FOUND',
            'timestamp': datetime.now().isoformat()
        }
    }), 404

@app.errorhandler(500)
def internal_error(error):
    """معالج خطأ 500"""
    return jsonify({
        'success': False,
        'error': {
            'message': 'خطأ داخلي في الخادم',
            'code': 'INTERNAL_ERROR',
            'timestamp': datetime.now().isoformat()
        }
    }), 500

# نقطة تشغيل التطبيق - متوافقة مع جميع المنصات السحابية
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
