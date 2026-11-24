import { useEffect, useState } from "react";
import { useAuth, useUser } from "@clerk/react-router";
import { Button } from "~/components/ui/button";
import { isTeamAdmin } from "~/sites/team/lib/team-access";
import { teamTitle } from "~/sites/team/lib/team-meta";

export const meta = () => [{ title: teamTitle("Push devices") }];

type Registration = {
  id: number;
  ios_home_screen: boolean;
  user_agent: string | null;
  created_at: string;
  updated_at: string;
};
type Member = { email: string; registrations: Registration[] };

function deviceLabel(registration: Registration) {
  const agent = registration.user_agent ?? "";
  if (registration.ios_home_screen) return /iPad/.test(agent) ? "iPad Home Screen app" : "iOS Home Screen app";
  if (/Android/.test(agent)) return "Android";
  if (/Windows/.test(agent)) return "Windows";
  if (/Macintosh/.test(agent)) return "Mac or iPad";
  return "Browser";
}

function RegistrationDetails({ registration }: { registration: Registration }) {
  return (
    <details className="border-t py-2 text-sm">
      <summary className="cursor-pointer py-1 font-medium">{deviceLabel(registration)} · #{registration.id}</summary>
      <dl className="grid gap-2 py-2 text-muted-foreground sm:grid-cols-2">
        <div><dt>First registered</dt><dd>{new Date(registration.created_at).toLocaleString()}</dd></div>
        <div><dt>Last registration update</dt><dd>{new Date(registration.updated_at).toLocaleString()}</dd></div>
        <div className="sm:col-span-2"><dt>Browser user agent</dt><dd className="break-words">{registration.user_agent || "Unknown"}</dd></div>
      </dl>
    </details>
  );
}

function Inventory() {
  const { getToken } = useAuth();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const token = await getToken();
        if (!token) throw new Error("Session expired");
        const response = await fetch("/api/sernia-ai/push/admin/registrations", {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok) throw new Error("Could not load registrations");
        const roster: Member[] = await response.json();
        if (!controller.signal.aborted) setMembers(roster);
      } catch {
        if (!controller.signal.aborted) setFailed(true);
      }
    }
    void load();
    return () => controller.abort();
  }, [getToken, revision]);

  return (
    <main className="mx-auto w-full max-w-3xl space-y-5 p-4 sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Push devices</h1>
        <Button variant="outline" size="sm" disabled={!members && !failed} onClick={() => {
          setMembers(null);
          setFailed(false);
          setRevision((value) => value + 1);
        }}>Refresh</Button>
      </div>
      <p className="text-sm text-muted-foreground">An iOS Home Screen registration means the app was installed when it registered. Current installation and last push delivery are unknown.</p>
      {failed && <p role="alert">Couldn’t load team registrations. Try refreshing.</p>}
      {!members && !failed && <p role="status">Loading team registrations…</p>}
      {members && (
        <>
          <p className="font-medium">{members.filter((member) => member.registrations.length > 0).length} of {members.length} team members have a push registration.</p>
          <div className="divide-y rounded-lg border">
            {members.map(({ email, registrations }) => (
              <section className="space-y-2 p-4" key={email}>
                <h2 className="break-words font-semibold">{email}</h2>
                <p className="text-sm text-muted-foreground">{registrations.length === 0 ? "No push registrations" : `${registrations.length} push registration${registrations.length === 1 ? "" : "s"}`}</p>
                {registrations.map((reg) => <RegistrationDetails key={reg.id} registration={reg} />)}
              </section>
            ))}
          </div>
        </>
      )}
    </main>
  );
}

export default function PushDevices() {
  const { user, isLoaded } = useUser();
  if (!isLoaded) return null;
  const primary = user?.primaryEmailAddress;
  if (!isTeamAdmin(primary?.emailAddress) || primary?.verification.status !== "verified") {
    return <p className="p-4">Administrator access required.</p>;
  }
  return <Inventory key={user?.id} />;
}
