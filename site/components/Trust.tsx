import Link from "next/link";

// Trust copy used on several pages, so the wording stays the same everywhere.

// After the install command: what the installer checks, and where to verify a download yourself.
export function VerifyNote() {
  return (
    <>
      The installer verifies the download against the release checksums (<code>SHA256SUMS</code>) before installing.
      You can check signed build provenance with <code>gh attestation verify</code>; see{" "}
      <Link href="/docs/security/#verify">Verify a download</Link>.
    </>
  );
}

// Which chats an AI app may read.
export function AiAccessNote() {
  return (
    <>
      You decide which chats your AI may read. Hide any chat from AI apps with <code>dotiphant ai hide CHAT</code>, or
      the toggle in the viewer, or switch to <code>dotiphant ai mode selected</code> to share only the chats you
      choose.
    </>
  );
}

// Where ChatGPT, claude.ai and other remote apps come up: the plain risk of remote access.
export function RemoteRisk() {
  return (
    <ul className="ticks ticks--plain">
      <li>
        Remote access is the riskiest way to use Dotiphant. It exposes your archive through a tunnel you run, on a
        public HTTPS address.
      </li>
      <li>Every app needs your approval before it can read anything, in your terminal or with a one-time pairing code.</li>
      <li>
        Limit what AI apps can see with <code>dotiphant ai</code>: hide single chats, or share only the chats you
        choose.
      </li>
      <li>
        If you don&apos;t need a remote app, don&apos;t set it up. Claude Desktop, Claude Code, Codex and Cursor run on
        your computer and need no tunnel.
      </li>
    </ul>
  );
}
