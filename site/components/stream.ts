// Messages from the made-up archive that `dotiphant demo` opens. The hero drifts them past the lens.
export type StreamMsg = { chat: string; who: string; text: string };

export const STREAM: StreamMsg[][] = [
  [
    { chat: "Acme x Northwind integration", who: "Maya Chen", text: "Kicking off the integration thread. Goal: Northwind settlement API live in Acme checkout by the end of the month." },
    { chat: "Acme x Northwind integration", who: "Me", text: "Can we do a short call on Friday to walk through the launch checklist?" },
    { chat: "Acme x Northwind integration", who: "Maya Chen", text: "Friday 15:00 UTC works for me." },
    { chat: "Acme x Northwind integration", who: "Jonas Weber", text: "GBP fix is deployed to staging. Settlement reports now also include the FX rate for each transaction." },
    { chat: "Acme x Northwind integration", who: "Me", text: "Proposal: launch on the 28th, with the 4th as fallback if the security review slips." },
  ],
  [
    { chat: "Helios Labs <> Orbit Ventures", who: "Ines Moreau", text: "Draft reviewed. Main concern: the cliff is too short for the team allocation." },
    { chat: "Acme x Northwind integration", who: "Maya Chen", text: "Marketing wants a joint announcement for launch day. Any objections?" },
    { chat: "Kestrel Protocol Builders", who: "Tomas Okafor", text: "Grant round 3 opens next Monday." },
    { chat: "Priya Raman", who: "Priya Raman", text: "Ping me if the schema is unclear. The settlement tables are the tricky part." },
    { chat: "Helios Labs <> Orbit Ventures", who: "Sam Adeyemi", text: "Term sheet v3 is attached, changes highlighted." },
  ],
  [
    { chat: "Acme x Northwind integration", who: "Maya Chen", text: "Works for us. Locking the 28th." },
    { chat: "Lumen Wallet partners", who: "Lena Fischer", text: "First question: do you support hardware wallets at launch?" },
    { chat: "Lumen Wallet partners", who: "Me", text: "Ledger yes, Trezor in Q2." },
    { chat: "Kestrel Protocol Builders", who: "Kenji Sato", text: "Mainnet upgrade scheduled for the 15th, 14:00 UTC." },
    { chat: "Acme x Northwind integration", who: "Lena Fischer", text: "Agreed. I'll update the launch checklist and share it before Friday's call." },
  ],
  [
    { chat: "Maya Chen", who: "Maya Chen", text: "Hey, quick one: is the 28th really realistic?" },
    { chat: "Maya Chen", who: "Me", text: "If the security review closes on time, yes." },
    { chat: "Lumen Wallet partners", who: "Lena Fischer", text: "Can we co-host a Twitter Space at launch?" },
    { chat: "Lumen Wallet partners", who: "Me", text: "Yes, let's aim for launch week." },
    { chat: "Tomas Okafor", who: "Tomas Okafor", text: "Add a bit more detail on milestone 2. Reviewers like a clear test plan." },
  ],
  [
    { chat: "Acme x Northwind integration", who: "Jonas Weber", text: "Production rate limits: 50 requests per second per merchant, burst 200." },
    { chat: "Kestrel Protocol Builders", who: "Kenji Sato", text: "Integration guide for wallets is up on the docs site." },
    { chat: "Acme x Northwind integration", who: "Lena Fischer", text: "Security questionnaire is done. Two open items: retention period for webhook payloads, and the pen test date." },
    { chat: "Helios Labs <> Orbit Ventures", who: "Sam Adeyemi", text: "Also: we're at the Lisbon conference next week. Anyone around?" },
    { chat: "Lumen Wallet partners", who: "Kenji Sato", text: "Crash fix is in build 412." },
  ],
];

// The seven messages that match "launch", in two chats. The lens shows them as whole search results.
export const MATCHES: StreamMsg[] = [
  { chat: "Acme x Northwind integration", who: "Me", text: "Proposal: launch on the 28th, with the 4th as fallback if the security review slips." },
  { chat: "Lumen Wallet partners", who: "Lena Fischer", text: "Can we co-host a Twitter Space at launch?" },
  { chat: "Acme x Northwind integration", who: "Maya Chen", text: "Marketing wants a joint announcement for launch day. Any objections?" },
  { chat: "Lumen Wallet partners", who: "Lena Fischer", text: "First question: do you support hardware wallets at launch?" },
  { chat: "Acme x Northwind integration", who: "Me", text: "Can we do a short call on Friday to walk through the launch checklist?" },
  { chat: "Lumen Wallet partners", who: "Me", text: "Yes, let's aim for launch week." },
  { chat: "Acme x Northwind integration", who: "Lena Fischer", text: "Agreed. I'll update the launch checklist and share it before Friday's call." },
];
