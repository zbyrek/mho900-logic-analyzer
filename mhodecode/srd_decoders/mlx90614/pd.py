##
## This file is part of the libsigrokdecode project.
##
## Copyright (C) 2012 Uwe Hermann <uwe@hermann-uwe.de>
##
## Modified on 2026-11-23 for MHO900 Logic Analyzer: temperatures are read
## from the datasheet's SMBus read word (repeated start with read address).
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, see <http://www.gnu.org/licenses/>.
##

import sigrokdecode as srd

# RAM addresses of the ambient and the two object temperatures.
temperature_commands = (0x06, 0x07, 0x08)

class Decoder(srd.Decoder):
    api_version = 3
    id = 'mlx90614'
    name = 'MLX90614'
    longname = 'Melexis MLX90614'
    desc = 'Melexis MLX90614 infrared thermometer protocol.'
    license = 'gplv2+'
    inputs = ['i2c']
    outputs = []
    tags = ['IC', 'Sensor']
    annotations = (
        ('celsius', 'Temperature / °C'),
        ('kelvin', 'Temperature / K'),
    )
    annotation_rows = (
        ('temps-celsius', 'Temperature / °C', (0,)),
        ('temps-kelvin', 'Temperature / K', (1,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 'IDLE'
        self.command = None
        self.data = []

    def start(self):
        self.out_ann = self.register(srd.OUTPUT_ANN)

    def putx(self, data):
        self.put(self.ss, self.es, self.out_ann, data)

    # Temperatures are SMBus read words of RAM addresses 0x06 to 0x08: address
    # with write, command, repeated start, address with read, low byte, high
    # byte, PEC. The top bit of the word is an error flag.
    def decode(self, ss, es, data):
        cmd, databyte = data

        if cmd in ('START', 'STOP'):
            self.state = 'IDLE'
            self.command = None
            self.data = []
        elif cmd == 'ADDRESS WRITE':
            self.state = 'GET COMMAND'
        elif cmd == 'DATA WRITE':
            self.command = databyte if self.state == 'GET COMMAND' else None
            self.state = 'COMMAND DONE'
        elif cmd == 'ADDRESS READ':
            self.state = 'GET TEMPERATURE' if self.command in temperature_commands else 'IDLE'
            self.data = []
        elif cmd == 'DATA READ' and self.state == 'GET TEMPERATURE':
            if len(self.data) == 0:
                self.ss = ss
            self.data.append(databyte)
            if len(self.data) == 2:
                self.es = es
                raw = self.data[0] | (self.data[1] << 8)
                if not raw & 0x8000:
                    kelvin = raw * 0.02
                    celsius = kelvin - 273.15
                    self.putx([0, ['Temperature: %3.2f °C' % celsius]])
                    self.putx([1, ['Temperature: %3.2f K' % kelvin]])
                self.state = 'IDLE'
