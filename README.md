Queria que mi deepseekCLI de sluirs tuviera una extensión de memoria a largo plazo como la que tiene hermesAgent, mi agente me terminó llevando a una solución que no quería, pero necesitaba.
Así nace ZoraMemory (asi que no te sorprendas si ves colecciones con el nombre 'hermes'), una extensión que utiliza una base de datos vectorial con ChromaDB y python con mem0.
Creando continuidad espacial (project, cwd, timestamp). Lo que permite continuidad narrativa para el agente. Optimizando tiempos de explicación. 
Aumento de tokens estimado: 20% , trade-off razonable si quieres memoria a largo plazo sin contaminar tu archivo deepseek.md

---

Todo también nace porque, el archivo Deepseek.md (encargado del tunning y memorias core) no deberia guardar información de un chat, ergo, el agente es el mismo en diferentes sesiones.
Si usas el comando /clear se limpia la sesión creando otra, perdiendo todo el contexto de la anterior. Esta extensión arregla eso, inyectando al inicio de cada sesión, recuerdos vectoriales, que, hacen que recuerde sorprendentemente
muchas cosas anteriores.

---

El sistema fue vibecodeado y depurado críticamente para solucionar una problemática especifica. Es 100% seguro de que hay cosas que iterar y mejorar, pero en el estado actual,
la extensión es funcional y permite almacenamiento local de memorias.

---

Ahora el agente tiene 3 fuentes de memoria:
1.-Deepseek.md -> Core, tunning, personalidad, aspectos clave.
2.-Contexto de la sesión -> Es lo que entiende mientras hablas con el CLI
3.-Recuerdos inyectados -> Memorias de turnos textuales en vectores

---
Creditos:
https://github.com/sluisr-dev/deepseek-cli fork de geminiCLI compatible con deepseek API
https://github.com/mem0ai/mem0 framework de capa de memoria
