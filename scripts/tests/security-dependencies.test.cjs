const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../..');
const mobileRequire = createRequire(path.join(root, 'apps/my-expo-app/package.json'));
const expoRequire = createRequire(mobileRequire.resolve('expo/package.json'));
const cliRequire = createRequire(expoRequire.resolve('@expo/cli/package.json'));
const metroRequire = createRequire(expoRequire.resolve('@expo/metro-config/package.json'));
const forge = cliRequire('node-forge');

// GHSA-86w9-cpqp-85rv: exercise the installed Expo dependency, not a copied parser.
// Keys and messages are synthetic, generated locally, and never leave this process.
for (const withNull of [true, false]) {
  test(`RSA rejects extra nested DigestAlgorithm elements (NULL=${withNull})`, () => {
    const { asn1, pki, md, oids } = forge;
    const { publicKey, privateKey } = pki.rsa.generateKeyPair({ bits: 1024, e: 0x10001 });
    const digest = md.sha256.create().update('local security regression fixture');
    const T = asn1.Type;
    const U = asn1.Class.UNIVERSAL;
    const algorithm = [asn1.create(U, T.OID, false, asn1.oidToDer(oids.sha256).getBytes())];
    if (withNull) algorithm.push(asn1.create(U, T.NULL, false, ''));
    const signature = () => {
      const info = asn1.create(U, T.SEQUENCE, true, [
        asn1.create(U, T.SEQUENCE, true, algorithm),
        asn1.create(U, T.OCTETSTRING, false, digest.digest().getBytes()),
      ]);
      return privateKey.sign(asn1.toDer(info).getBytes(), 'NONE');
    };
    assert.equal(publicKey.verify(digest.digest().getBytes(), signature()), true);
    algorithm.push(asn1.create(U, T.OCTETSTRING, false, 'unexpected nested data'));
    assert.throws(() => publicKey.verify(digest.digest().getBytes(), signature()), /DigestInfo/);
    assert.equal(publicKey.verify(digest.digest().getBytes(), privateKey.sign(digest)), true);
  });
}

test('Metro reads real mobile PNG dimensions through image-size 2', () => {
  const metroRoot = path.dirname(metroRequire.resolve('metro/package.json'));
  const { getAssetSize } = require(path.join(metroRoot, 'src/Assets.js'));
  const filename = path.join(root, 'apps/my-expo-app/assets/images/icon.png');
  assert.deepEqual(getAssetSize('png', fs.readFileSync(filename), filename), {
    width: 379,
    height: 379,
  });
});

test('Metro asset pipeline reads dimensions from filesystem paths', async () => {
  const metroRoot = path.dirname(metroRequire.resolve('metro/package.json'));
  const { getAssetData } = require(path.join(metroRoot, 'src/Assets.js'));
  const filename = path.join(root, 'apps/my-expo-app/assets/images/icon.png');
  const asset = await getAssetData(filename, 'assets/images/icon.png', [], 'ios', '/assets');
  assert.equal(asset.width, 379);
  assert.equal(asset.height, 379);
  assert.equal(asset.type, 'png');
});
