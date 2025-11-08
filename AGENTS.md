This repo has a NextJS frontend and a FastAPI backend. It also has an Expo app but not really using it much, it is not a priority right now.

See README.md for more context.

See .vscode/launch.json for how to run the different services (again, we can ignore the Expo app for now).


You might need to run `touch .env.development.local` to create the file if it doesn't exist. However, you shouldn't need to have anything in this file, since it is meant for my local physical laptop. When you are running on cloud, you should have the same values injected into the environment variables when you deploy. Check if your env variables are being injected correctly by running `echo "testsecret length: length:${#MY_TEST_SECRET}"` in the terminal.