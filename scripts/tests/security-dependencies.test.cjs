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

// GHSA-jqcg-44mw-7w3h: malformed IPv6 trust prefixes must not trust every IPv4 hop.
const webRequire = createRequire(path.join(root, 'apps/web-react-router/package.json'));
const expressRequire = createRequire(webRequire.resolve('express/package.json'));
const proxyaddr = expressRequire('proxy-addr');
test('proxy-addr rejects IPv4 spoofing through broad IPv6 trust subnets', () => {
  const request = { socket: { remoteAddress: '198.51.100.8' }, headers: { 'x-forwarded-for': '127.0.0.1' } };
  for (const subnet of ['::ffff:10.0.0.0/8', '::/1']) {
    assert.equal(proxyaddr(request, proxyaddr.compile(subnet)), '198.51.100.8');
  }
  request.socket.remoteAddress = '10.0.0.4';
  request.headers['x-forwarded-for'] = '203.0.113.2';
  assert.equal(proxyaddr(request, proxyaddr.compile('10.0.0.0/8')), '203.0.113.2');
});

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

const metroPackageRequire = createRequire(metroRequire.resolve('metro/package.json'));
const micromatchRequire = createRequire(metroPackageRequire.resolve('micromatch/package.json'));
const braces = micromatchRequire('braces');

// GHSA-vfj7-8cjw-p6xm: cap parser-produced AST depth before recursive walkers run.
for (const method of ['parse', 'compile', 'expand', 'stringify']) {
  test(`braces.${method} rejects excessive brace and parenthesis depth`, () => {
    for (const [open, close] of [['{', '}'], ['(', ')']]) {
      for (const depth of [101, 4000]) {
        assert.throws(
          () => braces[method](open.repeat(depth) + 'a,b' + close.repeat(depth)),
          { name: 'SyntaxError', message: /nesting depth.*100/ },
        );
      }
    }
  });
}

test('braces keeps normal, escaped, quoted, and flat patterns working', () => {
  assert.deepEqual(braces.expand('src/{api,web}/{a,b}.js'), [
    'src/api/a.js', 'src/api/b.js', 'src/web/a.js', 'src/web/b.js',
  ]);
  assert.doesNotThrow(() => braces.compile('{'.repeat(100) + 'a,b' + '}'.repeat(100)));
  assert.doesNotThrow(() => braces.compile('\\{'.repeat(200)));
  assert.doesNotThrow(() => braces.compile('"' + '{'.repeat(200) + '"'));
  assert.doesNotThrow(() => braces.compile('{a,b}'.repeat(200)));
});
