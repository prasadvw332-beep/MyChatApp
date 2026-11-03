// Load test: how many users can the chatbot serve, and where does it break?
//
//   k6 run deploy/loadtest/chat.js                                   # against http://localhost:8080/api
//   k6 run -e BASE=https://chat.contoso.com/api -e PEAK=50 deploy/loadtest/chat.js
//
// deploy/loadgen makes a little steady traffic for the dashboard; this is different: it ramps users up
// until something gives, and fails (exit code != 0) when the service-level objectives below are missed,
// so it can run in CI against a staging environment.
//
// What to watch in Grafana while it runs: which saturates first? For a CPU-only Ollama it's almost
// always the model (requests queue in Ollama, time to first token climbs), long before the API, Qdrant
// or PostgreSQL. That tells you what to scale: model replicas or a GPU (vLLM), not backend pods. The
// rate limiter will also start answering 429 per user: that's it working, which is why every virtual
// user here has its own account.

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const BASE = __ENV.BASE || "http://localhost:8080/api";
const PEAK = Number(__ENV.PEAK || 20);

const QUESTIONS = [
  "How many days of paid annual leave do full-time employees get?",
  "Which days must employees be in the office?",
  "Who founded the company, and when?",
  "Summarize the travel policy in two sentences.",
  "What is the process to report a security incident?",
];

const answerTime = new Trend("chat_answer_seconds", true);

export const options = {
  scenarios: {
    ramp: {
      executor: "ramping-vus",
      stages: [
        { duration: "1m", target: Math.ceil(PEAK / 4) }, // warm up: models loaded, caches filling
        { duration: "3m", target: PEAK },                // climb to the peak
        { duration: "3m", target: PEAK },                // hold it: does it stay stable?
        { duration: "1m", target: 0 },                   // ramp down: does it recover?
      ],
    },
  },
  // the SLOs: the test fails if any is missed
  thresholds: {
    http_req_failed: ["rate<0.02"],                       // < 2% errors (429s count: tune limits for the test user)
    "http_req_duration{name:documents}": ["p(95)<500"],   // cheap endpoints stay fast under load
    chat_answer_seconds: ["p(95)<30000"],                 // a whole answer within 30 s for 95% of questions
  },
};

// one account per virtual user, created on its first iteration (k6 keeps cookies per VU)
let loggedIn = false;

function login() {
  const username = `load${__VU}x${Date.now() % 100000}`;
  const res = http.post(`${BASE}/auth/register`, JSON.stringify({ username, password: "load-test-password" }), {
    headers: { "Content-Type": "application/json" },
  });
  check(res, { registered: (r) => r.status === 201 });
  loggedIn = res.status === 201;
}

export default function () {
  if (!loggedIn) login();

  http.get(`${BASE}/documents`, { tags: { name: "documents" } });

  const question = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const res = http.post(`${BASE}/chat`, JSON.stringify({ messages: [{ role: "user", content: question }] }), {
    headers: { "Content-Type": "application/json" },
    tags: { name: "chat" },
    timeout: "120s",
  });
  answerTime.add(res.timings.duration);
  check(res, {
    "chat 200": (r) => r.status === 200,
    "has an answer": (r) => (r.body || "").length > 20,
  });

  sleep(5 + Math.random() * 10); // people read the answer before asking again
}
