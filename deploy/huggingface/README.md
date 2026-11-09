---
title: Agent de campus
emoji: 🎓
colorFrom: blue
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
short_description: Agent IA d'une école fictive, chaque action confirmée
---

Public demo of the campus agent by Charbel Dib. Source code: https://github.com/charbel-dib/ensta_chatbot

This Space builds the `main` branch of the GitHub repository (see the Dockerfile). To deploy a new
version: push to GitHub, then *Settings → Factory rebuild* here.

Secrets to set in *Settings → Variables and secrets*: `ANTHROPIC_API_KEY` and `CAMPUS_SECRET_KEY`.
