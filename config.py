from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./abyat.db"
    secret_key: str
    access_token_minutes: int = 720
    admin_username: str = "admin"
    admin_password: str
    admin_full_name: str = "مدير النظام"
    tz_offset_hours: float = 3.0   # لحساب «اليوم» في اللقطات اليومية (3 = السعودية)

    # ---- المرحلة 3: التقارير والجدولة ----
    reports_dir: str = "./reports_data"      # مكان حفظ ملفات التقارير المولّدة (volume في Docker)
    reports_keep_days: int = 90              # مدة الاحتفاظ بالتقارير المولّدة
    scheduler_enabled: bool = True           # تشغيل مُجدوِل التقارير داخل الخادم
    smtp_host: str = ""                      # اتركه فارغاً لتعطيل الإرسال بالبريد
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # ---- المرحلة 3ج: قالب PDF ومكتبة الوسائط ----
    company_name: str = "شركة أسوار بلس العقارية"          # ترويسة ملف PDF القياسي
    company_subtitle: str = "للشقق المخدومة والفنادق"
    media_dir: str = "./media_data"            # مكان حفظ الصور والفيديوهات (volume في Docker)
    max_image_mb: int = 15
    max_video_mb: int = 150
    fonts_dir: str = "/srv/fonts"              # خط Tajawal لملف PDF (يُركَّب من web/fonts)


settings = Settings()
