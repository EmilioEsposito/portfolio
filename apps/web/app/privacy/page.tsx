import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export default function PrivacyPolicy() {
  return (
    <div className="container mx-auto py-8 max-w-4xl">
      <Card>
        <CardHeader>
          <CardTitle className="text-3xl font-bold">Privacy Policy</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <section>
            <h2 className="text-2xl font-semibold mb-4">Last Updated: {new Date().toLocaleDateString()}</h2>
            <p className="text-muted-foreground">This Privacy Policy describes how we collect, use, and handle your information when you use our services.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">Information We Collect</h3>
            <p className="mb-4">We collect information that you provide directly to us when you:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Create an account or sign in using Google OAuth</li>
              <li>Grant access to your Google Drive files</li>
              <li>Grant access to your Gmail account</li>
              <li>Use our email autoresponder features</li>
              <li>Interact with our services</li>
            </ul>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">Google API Services</h3>
            <p className="mb-4">Our application uses Google API Services. By using our service, you authorize us to:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Access and read files from your Google Drive (when explicitly shared)</li>
              <li>Access and send emails through your Gmail account (with your permission)</li>
              <li>Store necessary authentication tokens to provide our services</li>
            </ul>
            <p className="mt-4">We only store and process the minimum data necessary to provide our services. We do not sell your data to third parties.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">How We Use Your Information</h3>
            <p className="mb-4">We use the collected information to:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Provide, maintain, and improve our services</li>
              <li>Process and complete transactions</li>
              <li>Send you technical notices and support messages</li>
              <li>Respond to your comments and questions</li>
              <li>Protect against abuse and unauthorized access</li>
            </ul>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">Data Security</h3>
            <p>We implement appropriate security measures to protect your personal information. However, no method of transmission over the Internet is 100% secure. We cannot guarantee absolute security of your data.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">Your Rights</h3>
            <p className="mb-4">You have the right to:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Access your personal information</li>
              <li>Correct inaccurate data</li>
              <li>Request deletion of your data</li>
              <li>Withdraw consent for data processing</li>
              <li>Revoke access to Google services</li>
            </ul>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">Changes to This Policy</h3>
            <p>We may update this Privacy Policy from time to time. We will notify you of any changes by posting the new Privacy Policy on this page and updating the "Last Updated" date.</p>
          </section>

          <section>
            <h3 className="text-xl font-semibold mb-3">Contact Us</h3>
            <p>If you have questions about this Privacy Policy, please contact us.</p>
          </section>
        </CardContent>
      </Card>
    </div>
  )
} 