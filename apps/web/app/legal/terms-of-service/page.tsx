import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export default function TermsOfService() {
  return (
    <div className="container mx-auto py-8 max-w-4xl">
      <Card>
        <CardHeader>
          <CardTitle className="text-3xl font-bold">Terms of Service</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <section>
            <h2 className="text-2xl font-semibold mb-4">Last Updated: {new Date().toLocaleDateString()}</h2>
            <p className="text-muted-foreground">Please read these Terms of Service carefully before using our services.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">1. Acceptance of Terms</h3>
            <p>By accessing or using our services, you agree to be bound by these Terms of Service. If you do not agree to these terms, please do not use our services.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">2. Description of Service</h3>
            <p className="mb-4">Our service provides:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Email autoresponder functionality using Gmail API</li>
              <li>Integration with Google Drive for document access and processing</li>
              <li>Other related services and features</li>
            </ul>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">3. Google API Services</h3>
            <p className="mb-4">Our application integrates with Google APIs. By using our service, you:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Authorize us to access your Google services as specified during the OAuth process</li>
              <li>Agree to Google's Terms of Service and Privacy Policy</li>
              <li>Understand that you can revoke access at any time through your Google Account settings</li>
              <li>Acknowledge that we will only use your data in accordance with our Privacy Policy</li>
            </ul>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">4. User Responsibilities</h3>
            <p className="mb-4">You agree to:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Provide accurate information when using our services</li>
              <li>Maintain the security of your account credentials</li>
              <li>Use the services in compliance with all applicable laws</li>
              <li>Not misuse or abuse the services or any connected Google services</li>
            </ul>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">5. Intellectual Property</h3>
            <p>All content, features, and functionality of our services are owned by us and are protected by international copyright, trademark, and other intellectual property laws.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">6. Limitation of Liability</h3>
            <p>We provide our services "as is" without any warranty. We shall not be liable for any indirect, incidental, special, consequential, or punitive damages resulting from your use of our services.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">7. Service Modifications</h3>
            <p>We reserve the right to modify or discontinue our services at any time, with or without notice. We shall not be liable to you or any third party for any modification, suspension, or discontinuance of the service.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">8. Termination</h3>
            <p>We reserve the right to terminate or suspend your access to our services immediately, without prior notice, for any reason including, but not limited to, a breach of these Terms.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">9. Changes to Terms</h3>
            <p>We may update these Terms of Service from time to time. We will notify you of any changes by posting the new Terms on this page and updating the "Last Updated" date.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">10. Contact</h3>
            <p>If you have any questions about these Terms, please contact us.</p>
          </section>
        </CardContent>
      </Card>
    </div>
  )
} 