const express = require('express');
const SerialPort = require('serialport');

const app = express();

// open serial connection
const port = new SerialPort('/dev/serial0', {
  baudRate: 9600
});

app.post('/print', (req, res) => {
  port.write("Hello World\n", (err) => {
    if (err) {
      return res.status(500).send('Error');
    }
    res.send('Printed');
  });

});

app.listen(3000, () => {
  console.log('Server running on port 3000');
});
