import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("test", "routes/test.tsx"),
  route("chat-emilio", "routes/chat-emilio.tsx"),
  route("calendly", "routes/calendly.tsx"),
  route("multi-agent-chat", "routes/multi-agent-chat.tsx"),
  route("chat-weather", "routes/chat-weather.tsx"),
] satisfies RouteConfig;
