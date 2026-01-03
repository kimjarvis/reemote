# Scripts

Run mkdoc to generate static pages under `site`

```
uv run mkdocs build
```

Run script generate_openapi create a json file with the openapi schema.

This can be converted to a hml file by redocly.

On Debian to install npx 

```bash
sudo apt-get install npm -y
sudo npm install -g npx -y
sudo npm install -g npm@latest --force
```

Run redocly to generate file redoc-static.html

```bash
cd scripts
npx @redocly/cli build-docs openapi.json
```

Move the file into the site folder.

```bash
cp redoc-static.html ../site/redoc-static.html
mv redoc-static.html ../redoc-static.html
```