# Vinicius Tutoriais Apps

Repositório F-Droid personalizado do canal **Vinicius Tutoriais**.

## URL para o F-Droid Basic 2.0

`https://viniciustutors.github.io/vinicius-tutoriais-apps/fdroid/repo`

## Adicionar aplicativos depois

Edite somente o arquivo `apps.json`.

Exemplo:

```json
{
  "name": "Nome do aplicativo",
  "source": "https://github.com/desenvolvedor/projeto",
  "asset_regex": "\\.apk$"
}
```

O GitHub Actions procura a última Release do projeto, baixa o APK oficial, gera o repositório F-Droid e publica automaticamente.
