"""A window that writes the AFG record files of a signal, and says how to set the generator to play them."""
from __future__ import annotations

import os

from PyQt5 import QtCore, QtWidgets

from . import generator
from .i18n import tr
from .signal_form import SignalForm

SETTINGS_MIN_WIDTH = 420
WINDOW_SIZE = (960, 680)


class AfgFileWindow(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__(owner, QtCore.Qt.Window)
        self.owner = owner
        owner._bind(self.setWindowTitle, "AFG waveform files")
        layout = QtWidgets.QVBoxLayout(self)
        note = owner._label("Makes the same signal as the Generator tab, as files to load on the scope. It needs no connection.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.form = SignalForm(owner)
        # Side by side: all the settings stay in sight while the form is being filled in.
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.form, 0, QtCore.Qt.AlignTop)
        group = owner._group("Generator settings")
        group_layout = QtWidgets.QVBoxLayout(group)
        self.setup = QtWidgets.QPlainTextEdit()
        self.setup.setReadOnly(True)
        self.setup.setMinimumWidth(SETTINGS_MIN_WIDTH)
        group_layout.addWidget(self.setup)
        row.addWidget(group, 1)
        layout.addLayout(row, 1)
        self.save_button = QtWidgets.QPushButton()
        owner._bind(self.save_button.setText, "Save files…")
        owner._bind(self.save_button.setToolTip, "Writes one CSV file per line in use. The name you give gets the name of the line added.")
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        self.result = QtWidgets.QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.result)
        self.form.changed.connect(self.refresh)
        self.refresh()
        self.resize(*WINDOW_SIZE)

    def refresh(self):
        """What the form asks for now: the settings to enter, or why there is no signal yet."""
        self.result.clear()
        try:
            generated = self.form.build()
        except ValueError as exc:
            self.setup.setPlainText(str(exc))
            self.save_button.setEnabled(False)
            return
        self.setup.setPlainText(generator.setup_text(generated, generator.file_stem(generated.protocol)))
        self.save_button.setEnabled(True)

    def save(self):
        generated = self.form.build()
        folder = str(self.owner.settings.value("afg_file_folder", ""))
        path, _chosen = QtWidgets.QFileDialog.getSaveFileName(
            self, tr("Save AFG waveform files"), os.path.join(folder, generator.file_stem(generated.protocol) + ".csv"), tr("CSV files (*.csv)"),
            options=QtWidgets.QFileDialog.DontConfirmOverwrite)  # the files written are not named as this one: asked about below
        if not path:
            return
        folder, name = os.path.split(path)
        stem = os.path.splitext(name)[0]
        files = {os.path.join(folder, file_name): content for file_name, content in generator.record_files(generated, stem).items()}
        present = [os.path.basename(target) for target in files if os.path.exists(target)]
        if present and QtWidgets.QMessageBox.question(self, tr("Save AFG waveform files"), tr("Replace {files}?", files=", ".join(present))) != QtWidgets.QMessageBox.Yes:
            return
        try:
            for target, content in files.items():
                with open(target, "wb") as file:
                    file.write(content)
        except OSError as exc:
            self.result.setText(tr("ERROR: {message}", message=exc))
            return
        self.owner.settings.setValue("afg_file_folder", folder)
        self.setup.setPlainText(generator.setup_text(generated, stem))
        self.result.setText(tr("Saved in {folder}: {files}", folder=folder, files=", ".join(os.path.basename(target) for target in files)))
