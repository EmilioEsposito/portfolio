export type DeploymentEnvironment = "production" | "development" | "local" | "preview";

export function getDeploymentEnvironment(
  railwayEnvironmentName: string | undefined,
): DeploymentEnvironment {
  switch (railwayEnvironmentName?.toLowerCase()) {
    case "production":
      return "production";
    case "development":
      return "development";
    case "":
    case undefined:
      return "local";
    default:
      return "preview";
  }
}
