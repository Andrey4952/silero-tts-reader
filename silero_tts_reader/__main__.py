"""Entry point for silero-tts-reader."""
import sys
from silero_tts_reader.app import Application


def main():
    app = Application(sys.argv)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
