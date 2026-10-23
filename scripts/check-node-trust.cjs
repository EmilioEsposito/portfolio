// Offline inventory for Node's bundled/effective roots and the image OS bundle.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const tls = require('node:tls');
const { X509Certificate } = require('node:crypto');

function check(certificates, label) {
  assert.ok(certificates.length, `${label}: no certificates loaded`);
  assert.ok(!certificates.some(pem => new X509Certificate(pem).subject
    .split('\n').includes('CN=emSign ECC Root CA - G3')), `${label}: compromised G3 root remains`);
  console.log(`${label}: ${certificates.length} certificates; compromised G3 absent`);
}

console.log(`Node ${process.version}`);
check(tls.rootCertificates, 'Node bundled roots');
check(tls.getCACertificates('default'), 'Node effective default roots');
for (const path of process.platform === 'linux' ? ['/etc/ssl/cert.pem', '/etc/ssl/certs/ca-certificates.crt'] : []) {
  if (fs.existsSync(path)) {
    check(fs.readFileSync(path, 'utf8').match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g) || [], path);
  }
}
