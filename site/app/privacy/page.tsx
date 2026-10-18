import { pageMetadata } from "@/content/seo";
import { LegalPage } from "@/components/LegalPage";
import { COMPANY, COMPANY_ADDRESS, COMPANY_EMAIL } from "@/content/company";
import { GITHUB } from "@/lib";

export const metadata = pageMetadata({ path: "/privacy/", title: "Privacy policy", description: "How this website handles data. No cookies, cookieless visitor statistics.", type: "website" });

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy policy"
      intro={
        <p>
          This page covers this website. For the Dotiphant app itself, see the{" "}
          <a href={`${GITHUB}/blob/main/docs/privacy.md`}>app privacy notice</a>.
        </p>
      }
    >
      <h2>Who is responsible</h2>
      <p>
        {COMPANY}, {COMPANY_ADDRESS}. Email: <a href={`mailto:${COMPANY_EMAIL}`}>{COMPANY_EMAIL}</a>
      </p>

      <h2>No cookies, no advertising</h2>
      <p>
        This website sets no cookies and uses no advertising or embedded third-party content. Fonts and images are
        served from this website itself.
      </p>

      <h2 id="statistics">Visitor statistics</h2>
      <p>
        We count visits with Vercel Web Analytics, run by our hosting provider, Vercel. It sets no cookies and does not tie
        visits to IP addresses. A visitor is recognised only by a hash of the request, which is discarded after 24 hours, so
        visits cannot be linked across days or across other websites. For each page view it records the time, the
        page, the referring website, the country and city, the browser, the operating system and the device type. We
        only see these as totals. The legal basis is our legitimate interest in understanding which pages are useful
        (Art. 6(1)(f) GDPR). The Dotiphant app itself sends no telemetry.
      </p>

      <h2>Hosting and server logs</h2>
      <p>
        The site is hosted by Vercel Inc., 440 N Barranca Avenue #4133, Covina, CA 91723, United States. When you visit, the hosting provider
        records technical access data in server logs: your IP address, the date and time, the page requested, and
        usually your browser&apos;s user agent. This is needed to deliver the site and keep it secure. The legal basis
        is our legitimate interest under Art. 6(1)(f) GDPR. We can see these logs for one day; after that they are
        no longer available to us. Vercel may keep its own records for longer, as described in its{" "}
        <a href="https://vercel.com/legal/privacy-policy">privacy policy</a>.
      </p>

      <h2>Your rights</h2>
      <p>
        You have the right to access the personal data we hold about you, and to have it corrected, deleted or
        restricted. You can object to processing based on legitimate interest. You also have the right to lodge a
        complaint with a data protection supervisory authority.
      </p>
    </LegalPage>
  );
}
