# Сторонние компоненты и лицензии (ОТ-08)

| Компонент | Назначение | Лицензия |
|---|---|---|
| Flask | веб-сервер API | BSD-3-Clause |
| Pillow | работа с изображениями | HPND (PIL) |
| NumPy, SciPy | расчёты | BSD-3-Clause |
| scikit-image | демо-фото | BSD-3-Clause |
| OpenCV (opencv-python) | лица, inpaint, фильтры | Apache-2.0 |
| YuNet (`face_detection_yunet_2023mar.onnx`, OpenCV Zoo) | детектор лиц, качается при первом запуске | MIT |
| Streamlit (только старый интерфейс `app.py`) | десктоп-интерфейс | Apache-2.0 |
| PyTorch (необязательно) | ИИ-апскейл | BSD-3-Clause |
| Real-ESRGAN `realesr-general-x4v3` (необязательно, веса ~5 МБ) | ИИ-апскейл | BSD-3-Clause |
| Шрифт Nunito (Google Fonts) | интерфейс | SIL OFL 1.1 |
| OpenRouter API (необязательно, по ключу) | чат и правка фото | сервис, условия провайдера |
| Модели по умолчанию: `z-ai/glm-5.3-flash`, `openai/gpt-oss-20b`, `google/gemini-3.1-flash-image` | текст и изображения | условия соответствующих провайдеров |

Иллюстрации услуг, кот и робот нарисованы кодом (SVG) для этого прототипа; логотипы и фото picPac не используются.
Тестовые данные синтетические (`seed_data.py`).
