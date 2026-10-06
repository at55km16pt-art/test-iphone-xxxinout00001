import sys
import os
import wave
import sounddevice as sd
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QTextEdit, QLabel, QDialog, QListWidget, QMessageBox, QComboBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from openai import OpenAI
from elevenlabs.client import ElevenLabs
from elevenlabs import play

OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
ELEVENLABS_API_KEY = "YOUR_ELEVENLABS_API_KEY"

DEFAULT_VOICES = {
    "Adam (男性・標準英語)": "pNInz6obpgDQGcFmaJgB",
    "Rachel (女性・標準英語)": "21m00Tcm4TlvDq8ikWAM"
}

TEMP_AUDIO_FILE = "temp_recording.wav"

class AudioRecorderThread(QThread):
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, filename):
        super().__init__()
        self.filename = filename
        self.is_recording = False
        self.audio_frames = []

    def run(self):
        self.is_recording = True
        self.audio_frames = []

        def callback(indata, frames, time, status):
            if self.is_recording:
                self.audio_frames.append(indata.copy())

        try:
            # 1. 有効なマイクデバイスと対応サンプルレートを自動検索
            devices = sd.query_devices()
            input_device_id = None
            sample_rate = 44100

            for i, dev in enumerate(devices):
                if dev['max_input_channels'] > 0:
                    input_device_id = i
                    sample_rate = int(dev['default_samplerate'])
                    break

            if input_device_id is None:
                self.finished_signal.emit(False, "マイクデバイスが見つかりません。マイクが接続されているか確認してください。")
                return

            # 2. 明示的にデバイスIDとサンプルレートを指定して録音開始
            with sd.InputStream(device=input_device_id, samplerate=sample_rate, channels=1, dtype='int16', callback=callback):
                while self.is_recording:
                    self.msleep(50)

            # 3. 音声ファイルの保存
            if len(self.audio_frames) > 0:
                audio_data = np.concatenate(self.audio_frames, axis=0)
                with wave.open(self.filename, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(audio_data.tobytes())
                self.finished_signal.emit(True, "録音成功")
            else:
                self.finished_signal.emit(False, "音声データが取得できませんでした。")

        except Exception as e:
            self.finished_signal.emit(False, f"録音デバイスエラー: {str(e)}")

    def stop(self):
        self.is_recording = False


class HistoryDialog(QDialog):
    def __init__(self, history_list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("翻訳履歴")
        self.resize(400, 300)
        
        layout = QVBoxLayout()
        self.list_widget = QListWidget()
        for item in history_list:
            self.list_widget.addItem(f"JP: {item['ja']}\nEN: {item['en']}")
        layout.addWidget(self.list_widget)
        self.setLayout(layout)


class TranslationApp(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.history = []
        self.recorder_thread = None

    def init_ui(self):
        self.setWindowTitle("音声翻訳トークアプリ（デバイス自動検出版）")
        self.resize(500, 650)

        main_layout = QVBoxLayout()

        self.btn_history = QPushButton("📜 履歴を表示")
        self.btn_history.clicked.connect(self.show_history)
        main_layout.addWidget(self.btn_history, alignment=Qt.AlignRight)

        main_layout.addWidget(QLabel("【日本語表示（認識結果）】"))
        self.txt_japanese = QTextEdit()
        self.txt_japanese.setPlaceholderText("「話すボタン」を押しながら話すとここに日本語が表示されます...")
        main_layout.addWidget(self.txt_japanese)

        main_layout.addWidget(QLabel("【英語表示（翻訳結果）】"))
        self.txt_english = QTextEdit()
        self.txt_english.setPlaceholderText("日本語から翻訳された英語がここに表示されます...")
        main_layout.addWidget(self.txt_english)

        main_layout.addWidget(QLabel("【読み上げサンプルボイスの選択】"))
        self.combo_voice = QComboBox()
        for voice_name, voice_id in DEFAULT_VOICES.items():
            self.combo_voice.addItem(voice_name, voice_id)
        main_layout.addWidget(self.combo_voice)

        button_layout = QHBoxLayout()

        self.btn_talk = QPushButton("🎤 話す (長押し)")
        self.btn_talk.setStyleSheet("padding: 15px; font-weight: bold; background-color: #e1f5fe;")
        self.btn_talk.pressed.connect(self.start_recording)
        self.btn_talk.released.connect(self.stop_recording)
        button_layout.addWidget(self.btn_talk)

        self.btn_speak_en = QPushButton("🔊 英語トーク")
        self.btn_speak_en.setStyleSheet("padding: 15px; font-weight: bold; background-color: #e8f5e9;")
        self.btn_speak_en.clicked.connect(self.speak_english)
        button_layout.addWidget(self.btn_speak_en)

        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def start_recording(self):
        self.btn_talk.setText("🔴 録音中... (離すと停止)")
        self.recorder_thread = AudioRecorderThread(TEMP_AUDIO_FILE)
        self.recorder_thread.finished_signal.connect(self.on_recording_finished)
        self.recorder_thread.start()

    def stop_recording(self):
        self.btn_talk.setText("🎤 話す (長押し)")
        if self.recorder_thread and self.recorder_thread.isRunning():
            self.recorder_thread.stop()

    def on_recording_finished(self, success, message):
        if success:
            self.process_audio()
        else:
            QMessageBox.warning(self, "録音エラー", f"録音に失敗しました:\n{message}")

    def process_audio(self):
        try:
            client_openai = OpenAI(api_key=OPENAI_API_KEY)
            with open(TEMP_AUDIO_FILE, "rb") as audio_file:
                transcription = client_openai.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ja"
                )
            ja_text = transcription.text.strip()
            self.txt_japanese.setText(ja_text)

            if not ja_text:
                return

            response = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Translate the following Japanese text into natural spoken English."},
                    {"role": "user", "content": ja_text}
                ]
            )
            en_text = response.choices[0].message.content.strip()
            self.txt_english.setText(en_text)

            self.history.append({'ja': ja_text, 'en': en_text})

        except Exception as e:
            QMessageBox.critical(self, "APIエラー", f"認識・翻訳処理中にエラーが発生しました:\n{str(e)}")

    def speak_english(self):
        en_text = self.txt_english.toPlainText().strip()
        if not en_text:
            QMessageBox.information(self, "通知", "再生する英語テキストがありません。")
            return

        selected_voice_id = self.combo_voice.currentData()

        try:
            client_elevenlabs = ElevenLabs(api_key=ELEVENLABS_API_KEY)
            audio = client_elevenlabs.generate(
                text=en_text,
                voice=selected_voice_id,
                model="eleven_multilingual_v2"
            )
            play(audio)
        except Exception as e:
            QMessageBox.critical(self, "音声生成エラー", f"音声生成中にエラーが発生しました:\n{str(e)}")

    def show_history(self):
        dialog = HistoryDialog(self.history, self)
        dialog.exec_()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TranslationApp()
    window.show()
    sys.exit(app.exec_())