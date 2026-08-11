/**
 * Bounded FIFO tracker of outbound message IDs and delivery receipts.
 *
 * Used by the WhatsApp bridge to distinguish "echo of our own /send" from
 * "owner-typed message on the linked device" when forwarding `fromMe`
 * inbound events back to the Python adapter, and to expose receipt evidence
 * without opening a second Baileys socket.
 *
 * Eviction drops the oldest insertion-order entry when the cap is exceeded.
 * Re-remembering an existing id is a no-op for ordering (not LRU refresh).
 *
 * Heuristic limitation (intentional, documented for future debugging):
 * the tracker is in-memory only.  On bridge restart it is empty, so for the
 * brief window between restart and the first new outbound, any in-flight
 * delivery receipts of pre-restart sends would be classified as
 * owner-typed.  The TTL on owner-driven plugin actions (e.g. handover
 * sliding TTL) bounds blast radius; persisting would not be worth the
 * extra complexity / disk churn.
 */

function deliveryStatusBucket(value) {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (!normalized) return '';
  if (/^(2|3|4|5|server_ack|server|sent|ack|delivery_ack|delivered|read|played)$/.test(normalized)) {
    return 'accepted_or_delivered';
  }
  if (/^(0|error|failed)$/.test(normalized)) return 'failed';
  if (/^(1|pending)$/.test(normalized)) return 'pending';
  return 'unknown';
}

export function createOutboundIdTracker(maxSize = 512) {
  if (!Number.isInteger(maxSize) || maxSize < 1) {
    throw new RangeError('createOutboundIdTracker: maxSize must be a positive integer');
  }
  const ids = new Map();
  const waiters = new Map();

  function result(id, timeout = false) {
    const state = ids.get(id);
    return {
      observed: state?.observed === true,
      source: state?.source || null,
      statusBucket: state?.observed ? state.statusBucket : (timeout ? 'timeout' : (state?.statusBucket || 'pending')),
      updateCount: state?.updateCount || 0,
    };
  }

  function settle(id) {
    for (const resolve of waiters.get(id) || []) resolve(result(id));
    waiters.delete(id);
  }

  function remember(id) {
    const key = String(id || '').trim();
    if (!key || ids.has(key)) return;
    ids.set(key, null);
    while (ids.size > maxSize) {
      const oldest = ids.keys().next().value;
      settle(oldest);
      ids.delete(oldest);
    }
  }

  function has(id) {
    return Boolean(id) && ids.has(id);
  }

  function size() {
    return ids.size;
  }

  function observe(source, event = {}) {
    const key = event?.key || event?.message?.key || {};
    const id = String(key?.id || '').trim();
    if (!ids.has(id)) return false;
    const state = ids.get(id) || { observed: false, source: null, statusBucket: 'pending', updateCount: 0 };
    ids.set(id, state);
    const bucket = deliveryStatusBucket(event?.status ?? event?.update?.status ?? event?.receipt?.status);
    state.updateCount += 1;
    const receiptObserved = source === 'message-receipt.update' && bucket !== 'failed' && bucket !== 'pending';
    if (bucket === 'accepted_or_delivered' || receiptObserved) {
      state.observed = true;
      state.source = source;
      state.statusBucket = bucket || 'receipt_observed';
      settle(id);
      return true;
    }
    state.statusBucket = bucket || state.statusBucket;
    return false;
  }

  function waitFor(id, waitMs = 0) {
    const key = String(id || '').trim();
    if (!ids.has(key) || ids.get(key)?.observed || waitMs <= 0) return Promise.resolve(result(key));
    return new Promise((resolve) => {
      const pending = waiters.get(key) || new Set();
      let timer;
      const done = (value) => {
        clearTimeout(timer);
        pending.delete(done);
        if (pending.size === 0) waiters.delete(key);
        resolve(value);
      };
      pending.add(done);
      waiters.set(key, pending);
      timer = setTimeout(() => done(result(key, true)), Math.min(30000, Math.max(1, Number(waitMs) || 1)));
    });
  }

  return { remember, has, size, observe, waitFor };
}
