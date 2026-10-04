import { LegalShell, Section, List } from "@/components/app/legal";

export const metadata = { title: "Privacy Policy | GeM-Sentinel" };

export default function PrivacyPage() {
  return (
    <LegalShell title="Privacy Policy" updated="27 September 2026">
      <Section title="1. About this policy">
        <p>
          This Privacy Policy explains how GeM-Sentinel, the bid compliance verification platform operated
          by Central Public Chemical Limited (CPCL), Ministry of Petroleum and Gas, Government of India,
          collects, uses, discloses, and protects the information you provide when you apply for tenders
          or perform official duties on the platform.
        </p>
        <p>
          By creating an account or submitting a tender application, you agree to the practices described
          in this policy.
        </p>
      </Section>

      <Section title="2. Information we collect">
        <List
          items={[
            <>
              <b>Account information:</b> your full name, organisation name, registered address, email
              address, and mobile number (verified by one-time password).
            </>,
            <>
              <b>Registration identifiers:</b> GSTIN, PAN, and UDYAM registration number where applicable,
              used to link your application to official records.
            </>,
            <>
              <b>Application data:</b> tender applications, technical and financial documents,
              declarations, and responses to clarification requests that you submit.
            </>,
            <>
              <b>Verification responses:</b> data returned by government sources (GST portal, MCA, EPFO,
              Udyam) that we retrieve only after you give explicit consent for each document category.
            </>,
            <>
              <b>System records:</b> IP address, device identifier, and timestamps, used for one-time
              password delivery, trusted-device sign-in, and the tamper-evident audit log.
            </>,
          ]}
        />
      </Section>

      <Section title="3. How we use your information">
        <List
          items={[
            "To process and evaluate your tender application.",
            "To verify documents against government sources, with your consent.",
            "To prevent fraud, detect conflicts of interest, and enforce the bidder blacklist.",
            "To maintain the audit trail required under public procurement rules.",
            "To send transactional messages such as one-time passwords and status changes.",
          ]}
        />
      </Section>

      <Section title="4. Consent and verification">
        <p>
          Document verification is consent-based. You choose which document categories may be checked
          against external government sources, and each verification run records the consent you gave.
          You may withdraw consent at any time before your application is opened for evaluation;
          documents already verified remain on record as part of the application.
        </p>
      </Section>

      <Section title="5. Disclosure of your information">
        <p>Your information is shared only with:</p>
        <List
          items={[
            "Tender evaluation officers and committee members assigned to your tender, scoped to that tender.",
            "The Vigilance department or Competent Authority when an override or fraud referral is made.",
            "Statutory authorities where the law requires disclosure.",
          ]}
        />
        <p>
          We do not sell your personal information. We do not use your information for advertising or
          marketing.
        </p>
      </Section>

      <Section title="6. Retention">
        <p>
          Applications, verification reports, and audit records are retained for five years from the
          closure of the tender or completion of the resulting contract, whichever is later, in line with
          government records management practice.
        </p>
      </Section>

      <Section title="7. Security">
        <p>
          Data is encrypted in transit. Access is role-based: each user sees only the tenders and
          applications assigned to them. Sensitive actions require re-authentication, and every material
          action is recorded in a hash-chained audit log that makes tampering detectable.
        </p>
      </Section>

      <Section title="8. Your rights">
        <List
          items={[
            "Access a copy of the information we hold about you.",
            "Request correction of inaccurate information.",
            "Appeal an eligibility decision through the in-portal appeal process.",
            "Withdraw verification consent before evaluation, as described in Section 4.",
          ]}
        />
      </Section>

      <Section title="9. Cookies and tracking">
        <p>
          The platform uses only technical cookies required for your session and language preference.
          No third-party trackers or analytics services are used.
        </p>
      </Section>

      <Section title="10. Grievance">
        <p>
          For questions or complaints about this policy, write to the Grievance Officer at
          grievance.gemsentinel@cpcl.gov.in. We respond within 30 days.
        </p>
      </Section>

      <Section title="11. Changes to this policy">
        <p>
          If we change this policy, the updated version and its date will be posted on this page.
          Material changes take effect only after this posting.
        </p>
      </Section>
    </LegalShell>
  );
}
