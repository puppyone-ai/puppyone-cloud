import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';
import { egressRules } from './fly-egress.mjs';

const SEALED = 'meta skuid 1000 counter reject with icmpx type admin-prohibited';

test('anything but an explicit open keeps the task user on loopback', () => {
  for (const value of [undefined, '', '1', 'true', 'OPEN', ' open']) {
    assert.ok(egressRules(value).includes(SEALED), String(value));
  }
});

test('open keeps every private range closed and leaves the rest to the accept policy', () => {
  const rules = egressRules('open');
  assert.ok(!rules.includes(SEALED));
  for (const range of [
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16', '100.64.0.0/10',
    'fc00::/7', 'fe80::/10',
  ]) {
    assert.ok(rules.includes(range), range);
  }
  assert.ok(rules.indexOf('127.0.0.0/8 accept') < rules.indexOf('10.0.0.0/8'));
});

test('a firewall that cannot be installed stops the boot', () => {
  const init = new URL('./fly-init.mjs', import.meta.url).pathname;
  const boot = spawnSync(process.execPath, [init], { env: { PATH: '' }, encoding: 'utf8' });
  assert.notEqual(boot.status, 0);
  assert.match(boot.stderr, /nft/);
});
