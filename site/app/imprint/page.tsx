import { pageMetadata } from "@/content/seo";
import { LegalPage } from "@/components/LegalPage";
import { COMPANY, COMPANY_ADDRESS, COMPANY_EMAIL } from "@/content/company";

export const metadata = pageMetadata({ path: "/imprint/", title: "Imprint", description: "Legal notice for the Dotiphant website.", type: "website" });

export default function ImprintPage() {
  return (
    <LegalPage title="Imprint">
      <p>
        {COMPANY}
        <br />
        {COMPANY_ADDRESS}
      </p>
      <p>
        Email: <a href={`mailto:${COMPANY_EMAIL}`}>{COMPANY_EMAIL}</a>
      </p>

      <h2>Consumer dispute resolution</h2>
      <p>We are not willing or obliged to take part in dispute resolution proceedings before a consumer arbitration board.</p>
    </LegalPage>
  );
}
