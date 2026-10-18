import { Header } from "@/components/Header";
import { Hero } from "@/components/Hero";
import { Ask } from "@/components/Ask";
import { Statement } from "@/components/Statement";
import { Rail } from "@/components/Rail";
import { HowItWorks } from "@/components/HowItWorks";
import { Privacy } from "@/components/Privacy";
import { WorksWith } from "@/components/WorksWith";
import { Comparison } from "@/components/Comparison";
import { Demo } from "@/components/Demo";
import { Faq } from "@/components/Faq";
import { Footer } from "@/components/Footer";
import { Reveal } from "@/components/Reveal";
import { JsonLd } from "@/components/JsonLd";
import { FAQ, plainAnswer } from "@/content/faq";
import { DESCRIPTION, HOME_TITLE, faqPage, graph, organization, pageMetadata, softwareApplication, website } from "@/content/seo";

export const metadata = pageMetadata({
  path: "/",
  title: HOME_TITLE,
  description: DESCRIPTION,
  type: "website",
  absoluteTitle: true,
});

const structured = graph(
  softwareApplication,
  organization,
  website,
  faqPage(FAQ.map((item) => ({ q: item.q, text: plainAnswer(item) }))),
);

export default function Home() {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Header overHero />
      <main id="main">
        <Hero />
        <Statement />
        <Ask />
        <Rail />
        <HowItWorks />
        <Privacy />
        <WorksWith />
        <Comparison />
        <Demo />
        <Faq />
      </main>
      <Footer />
      <Reveal />
      <JsonLd data={structured} />
    </>
  );
}
