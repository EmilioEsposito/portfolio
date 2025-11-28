import type { Route } from "./+types/examples.loader-api-call";
import { getBackendUrl } from "~/middleware/api-proxy";

/**
 * Example: Server-side API call in a loader
 * 
 * The apiProxyMiddleware handles client-side fetches, but loaders run on the server
 * and need to use absolute URLs with the getBackendUrl() helper.
 */
export async function loader({ request }: Route.LoaderArgs) {
  const backendUrl = getBackendUrl();
  
  // Use absolute URL for server-side fetch
  const response = await fetch(`${backendUrl}/api/examples/data`, {
    headers: {
      // Forward any necessary headers (like auth tokens)
      Authorization: request.headers.get("Authorization") || "",
    },
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.statusText}`);
  }

  const data = await response.json();
  return { data };
}

export default function LoaderApiCallExample({ loaderData }: Route.ComponentProps) {
  return (
    <div className="container mx-auto py-8 px-4">
      <h1 className="text-3xl font-bold mb-4">Loader API Call Example</h1>
      <p className="mb-4">
        This page demonstrates how to make API calls from a server-side loader.
      </p>
      <pre className="bg-gray-100 dark:bg-gray-800 p-4 rounded-lg overflow-auto">
        {JSON.stringify(loaderData.data, null, 2)}
      </pre>
      
      <div className="mt-8 p-4 border rounded-lg">
        <h2 className="text-xl font-semibold mb-2">Key Points:</h2>
        <ul className="list-disc list-inside space-y-2">
          <li>
            <strong>Client-side fetches:</strong> Use relative URLs like{" "}
            <code className="bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded">
              fetch("/api/...")
            </code>
            {" "}(handled by middleware proxy)
          </li>
          <li>
            <strong>Server-side loaders:</strong> Use{" "}
            <code className="bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded">
              getBackendUrl()
            </code>{" "}
            helper for absolute URLs
          </li>
          <li>
            Railway internal networking: Uses{" "}
            <code className="bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded">
              http://fastapi.railway.internal:8000
            </code>
          </li>
        </ul>
      </div>
    </div>
  );
}

