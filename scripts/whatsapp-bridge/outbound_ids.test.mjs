import test from 'node:test';
import assert from 'node:assert/strict';

import { createOutboundIdTracker } from './outbound_ids.js';

test('remembers and recognises an outbound id', () => {
  const tracker = createOutboundIdTracker();
  tracker.remember('msg-1');
  assert.equal(tracker.has('msg-1'), true);
  assert.equal(tracker.has('msg-2'), false);
});

test('ignores empty / falsy ids', () => {
  const tracker = createOutboundIdTracker();
  tracker.remember(undefined);
  tracker.remember('');
  tracker.remember(null);
  assert.equal(tracker.size(), 0);
  assert.equal(tracker.has(''), false);
  assert.equal(tracker.has(undefined), false);
});

test('evicts oldest entry once the cap is exceeded', () => {
  const tracker = createOutboundIdTracker(3);
  tracker.remember('a');
  tracker.remember('b');
  tracker.remember('c');
  tracker.remember('d'); // cap=3 → 'a' should be evicted
  assert.equal(tracker.has('a'), false);
  assert.equal(tracker.has('b'), true);
  assert.equal(tracker.has('c'), true);
  assert.equal(tracker.has('d'), true);
  assert.equal(tracker.size(), 3);
});

test('cap holds across many inserts (bounded memory)', () => {
  const tracker = createOutboundIdTracker(8);
  for (let i = 0; i < 100; i += 1) {
    tracker.remember(`id-${i}`);
  }
  assert.equal(tracker.size(), 8);
  // Oldest (id-0..id-91) should be gone, latest 8 retained.
  assert.equal(tracker.has('id-0'), false);
  assert.equal(tracker.has('id-91'), false);
  assert.equal(tracker.has('id-92'), true);
  assert.equal(tracker.has('id-99'), true);
});

test('re-remembering an existing id does not promote it (FIFO, not LRU)', () => {
  // Insertion-order semantics: re-adding doesn't move it forward in
  // Set iteration order. This is intentional — we don't need recency,
  // just bounded membership.  Pin the actual behaviour so future
  // refactors don't accidentally introduce LRU refresh semantics.
  const tracker = createOutboundIdTracker(2);
  tracker.remember('a');
  tracker.remember('b');
  tracker.remember('a'); // no-op for ordering
  tracker.remember('c'); // evicts 'a' (oldest by insertion)
  assert.equal(tracker.has('a'), false);
  assert.equal(tracker.has('b'), true);
  assert.equal(tracker.has('c'), true);
});

test('rejects non-positive maxSize', () => {
  assert.throws(() => createOutboundIdTracker(0), RangeError);
  assert.throws(() => createOutboundIdTracker(-1), RangeError);
  assert.throws(() => createOutboundIdTracker(1.5), RangeError);
});

test('delivery tracker waits for a matching provider receipt', async () => {
  const tracker = createOutboundIdTracker();
  tracker.remember('msg-1');
  const waiting = tracker.waitFor('msg-1', 1000);
  assert.equal(tracker.observe('messages.update', {
    key: { id: 'msg-1', remoteJid: '15551234567@s.whatsapp.net', fromMe: true },
    update: { status: 3 },
  }), true);
  assert.deepEqual(await waiting, {
    observed: true,
    source: 'messages.update',
    statusBucket: 'accepted_or_delivered',
    updateCount: 1,
  });
});

test('delivery tracker does not confirm pending or unrelated updates', async () => {
  const tracker = createOutboundIdTracker();
  tracker.remember('msg-1');
  assert.equal(tracker.observe('messages.update', { key: { id: 'other' }, update: { status: 3 } }), false);
  assert.equal(tracker.observe('messages.update', { key: { id: 'msg-1' }, update: { status: 1 } }), false);
  assert.deepEqual(await tracker.waitFor('msg-1', 0), {
    observed: false,
    source: null,
    statusBucket: 'pending',
    updateCount: 1,
  });
});

test('delivery tracker times out without inventing confirmation', async () => {
  const tracker = createOutboundIdTracker();
  tracker.remember('msg-1');
  const status = await tracker.waitFor('msg-1', 1);
  assert.equal(status.observed, false);
  assert.equal(status.statusBucket, 'timeout');
});
