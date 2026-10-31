// App state shared by the feature modules. It is one object so that every module sees the current
// values: a module can't reassign another module's exported `let`, but it can change a property.
export const state = {
  chats: [], // this user's chats (loaded from /chats at startup)
  chat: null, // the open chat
  controller: null, // AbortController, set while a reply is streaming
  agentMode: false, // replies come from /agent/chat, where the model picks tools itself
};
