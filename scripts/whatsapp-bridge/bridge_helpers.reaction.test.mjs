/**
 * Unit tests for buildReactionPayload: the WAMessageKey a /react call sends.
 *
 * A group reaction only lands when its key names the message's author, so the
 * key Baileys delivered (kept in the bounded message store) must be used as-is;
 * the store lookup must also refuse a message id that belongs to another chat.
 */

import { strict as assert } from 'node:assert';

import { buildReactionPayload, createBoundedMessageStore } from './bridge_helpers.js';

const GROUP = '120363000000000000@g.us';
const DM = '15551234567@s.whatsapp.net';

function storeWith(...messages) {
  const store = createBoundedMessageStore(8);
  for (const msg of messages) store.remember(msg);
  return store;
}

// A stored group message is reacted to with the exact key Baileys delivered.
{
  const key = { remoteJid: GROUP, id: 'GRP1', fromMe: false, participant: '99887766@lid' };
  const { jid, content } = buildReactionPayload({
    chatId: GROUP, messageId: 'GRP1', emoji: '❤️', messageStore: storeWith({ key, message: {} }),
  });
  assert.equal(jid, GROUP);
  assert.deepEqual(content, { react: { text: '❤️', key } });
}

// A device-qualified chat id still matches the stored chat.
{
  const key = { remoteJid: DM, id: 'DM1', fromMe: false };
  const { content } = buildReactionPayload({
    chatId: '15551234567:7@s.whatsapp.net', messageId: 'DM1', emoji: '👍',
    messageStore: storeWith({ key, message: {} }),
  });
  assert.equal(content.react.key, key);
}

// A message id from another chat is refused, never reacted to in the wrong chat.
assert.throws(
  () => buildReactionPayload({
    chatId: DM, messageId: 'GRP1', emoji: '👍',
    messageStore: storeWith({ key: { remoteJid: GROUP, id: 'GRP1', participant: '1@lid' }, message: {} }),
  }),
  /not in chat/,
);

// A DM that aged out of the store is rebuilt from its ids; self-chat messages are fromMe.
assert.deepEqual(
  buildReactionPayload({ chatId: DM, messageId: 'OLD', emoji: '😂', messageStore: storeWith() }),
  { jid: DM, content: { react: { text: '😂', key: { remoteJid: DM, id: 'OLD', fromMe: false } } } },
);
assert.equal(
  buildReactionPayload({ chatId: DM, messageId: 'OLD', emoji: '😂', messageStore: storeWith(), selfChat: true })
    .content.react.key.fromMe,
  true,
);

// A group message outside the store has no known author, so it cannot be reacted to.
assert.throws(
  () => buildReactionPayload({ chatId: GROUP, messageId: 'GONE', emoji: '👍', messageStore: storeWith() }),
  /group reaction needs its author/,
);

// "" removes the reaction; a missing emoji or id is a request error.
assert.equal(
  buildReactionPayload({ chatId: DM, messageId: 'X', emoji: '', messageStore: storeWith() }).content.react.text,
  '',
);
assert.throws(() => buildReactionPayload({ chatId: DM, messageId: 'X', messageStore: storeWith() }), /emoji/);
assert.throws(() => buildReactionPayload({ chatId: DM, emoji: '👍', messageStore: storeWith() }), /required/);

console.log('bridge_helpers.reaction.test.mjs: all assertions passed');
