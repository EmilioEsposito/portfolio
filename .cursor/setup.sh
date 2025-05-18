# install uv 
curl -LsSf https://astral.sh/uv/install.sh | sh
echo 'eval "$(uv generate-shell-completion bash)"' >> ~/.bashrc

# install python 3.11
uv python install 3.11

cd /workspace
uv venv
uv sync 


curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - \
    && sudo apt-get install -y nodejs \
    && sudo npm install -g pnpm

pnpm install


echo ">>> Now manually create .env.development.local file in the root of the project "