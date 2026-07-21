"""Silero TTS engine using PyTorch package (.pt model)."""
from __future__ import annotations

import re
import threading
from pathlib import Path

import numpy as np


class TTSLoadError(Exception):
    """Raised when the Silero model cannot be loaded."""


class TTSEngine:
    """Synthesizes speech from text using a Silero PyTorch packaged model.

    Thread-safe: synthesize() may be called from worker threads.
    """

    _ABBR = re.compile(
        r'\b(?:г|гр|д|доц|зам|зав|им|ин|кг|кв|км|л|м|мг|мл|мм|'
        r'млн|млрд|напр|оз|пр|проф|р|руб|рис|с|см|стр|т|тел|'
        r'тыс|ул|ф|чел|эт)\.'
    )
    _SENTENCE_END = re.compile(r'(?<=[.!?…])\s+')

    def __init__(self, model_path: str, sample_rate: int = 48000) -> None:
        self._model_path = model_path
        self._sample_rate = sample_rate
        self._model = None
        self._speakers: list[str] = []
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load the Silero .pt model. Raises TTSLoadError on failure."""
        import torch  # noqa: PLC0415

        path = Path(self._model_path)
        if not path.exists():
            raise TTSLoadError(
                f"Модель не найдена: {path}\n\n"
                "Скачайте модель:\n"
                "  mkdir -p ~/.local/share/silero-tts-reader\n"
                "  wget -O ~/.local/share/silero-tts-reader/v5_ru.pt \\\n"
                "    https://models.silero.ai/models/tts/ru/v5_ru.pt\n\n"
                "Или запустите install.sh"
            )

        try:
            with self._lock:
                # Silero v4 distributes models as torch.package archives
                imp = torch.package.PackageImporter(str(path))
                model = imp.load_pickle("tts_models", "model")
                # TTSModelMultiAcc_v3 is NOT nn.Module — don't call .eval()
                self._model = model
                self._speakers = (
                    list(model.speakers)
                    if hasattr(model, "speakers")
                    else list(model.get_speakers())
                    if hasattr(model, "get_speakers")
                    else ["aidar", "baya", "kseniya", "xenia", "eugene", "random"]
                )
        except TTSLoadError:
            raise
        except Exception as exc:
            raise TTSLoadError(f"Ошибка загрузки модели: {exc}") from exc

    def get_available_speakers(self) -> list[str]:
        with self._lock:
            return list(self._speakers)

    def synthesize(self, text: str, speaker: str) -> np.ndarray:
        """Synthesize text → float32 numpy array at self._sample_rate Hz."""
        import torch  # noqa: PLC0415

        with self._lock:
            if self._model is None:
                raise RuntimeError("Движок не инициализирован. Вызовите load() сначала.")

            if self._speakers and speaker not in self._speakers:
                speaker = "xenia" if "xenia" in self._speakers else self._speakers[0]

            from .text_processor import TextProcessor
            import re
            if re.search(r"\d", text):
                print(f"[TTSEngine] Предупреждение: получен текст с цифрами ({text!r}). Применяю автономный численный транслитератор перед apply_tts...", flush=True)
                text = TextProcessor.convert_numbers_to_words(text)

            if TextProcessor.has_non_cyrillic(text):
                print(f"[TTSEngine] Предупреждение: получен текст с латиницей ({text!r}). Применяю защитную офлайн-транслитерацию перед apply_tts...", flush=True)
                text = TextProcessor.transliterate_offline(text)

            print(f"[TTSEngine] Вызов apply_tts(speaker={speaker!r}): {text!r}", flush=True)
            with torch.no_grad():
                audio = self._model.apply_tts(
                    text=text,
                    speaker=speaker,
                    sample_rate=self._sample_rate,
                    put_accent=True,
                    put_yo=True,
                )
            res = audio.numpy().astype(np.float32)
            print(f"[TTSEngine] Сгенерирован аудиомассив длины {len(res)} сэмплов ({len(res)/self._sample_rate:.2f} сек)", flush=True)
            return res

    def split_into_segments(self, text: str, max_chars: int = 800) -> list[str]:
        """Split text into speakable segments by sentence boundaries."""
        protected = self._ABBR.sub(lambda m: m.group().replace(".", "·"), text)
        raw_sentences = self._SENTENCE_END.split(protected)

        segments: list[str] = []
        current = ""

        for sent in raw_sentences:
            sent = sent.replace("·", ".").strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > max_chars and current:
                segments.append(current.strip())
                current = sent
            else:
                current = (current + " " + sent).strip() if current else sent

        if current:
            segments.append(current.strip())

        return segments or [text.strip()]

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._model is not None
