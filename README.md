**Intro**

Queria que mi deepseekCLI de sluirs tuviera una extensión de memoria a largo plazo como la que tiene hermesAgent, mi agente me terminó llevando a una solución que no quería, pero necesitaba.
Así nace ZoraMemory (asi que no te sorprendas si ves colecciones con el nombre 'hermes'), una extensión que utiliza una base de datos vectorial con ChromaDB y python con mem0.
Creando continuidad espacial (project, cwd, timestamp). Lo que permite continuidad narrativa para el agente. Optimizando tiempos de explicación. 
Aumento de tokens estimado: 20% , trade-off razonable si quieres memoria a largo plazo sin contaminar tu archivo deepseek.md

---
**Razones**

Todo también nace porque, el archivo Deepseek.md (encargado del tunning y memorias core) no deberia guardar información de un chat, ergo, el agente es el mismo en diferentes sesiones.
Si usas el comando /clear se limpia la sesión creando otra, perdiendo todo el contexto de la anterior. Esta extensión arregla eso, inyectando al inicio de cada sesión, recuerdos vectoriales, que, hacen que recuerde sorprendentemente
muchas cosas anteriores.

---
**Desarrollo**

El sistema fue vibecodeado y depurado críticamente para solucionar una problemática especifica. Es 100% seguro de que hay cosas que iterar y mejorar, pero en el estado actual,
la extensión es funcional y permite almacenamiento local de memorias.

---
**Funcionamiento**
Para crear la extensión para deepseekCLI, que es un fork del descanse en paz de geminiCLI, se debe crear un hook, que "intercepte" el prompt antes de enviarlo para así enriquecer nuestro prompt con memorias vectoriales.

*Flujo con la extensión:*

Session_start -> Consultar memoria (inyección) -> [Prompt_usuario_enriquecido] -> API -> Respuesta mejorada gracias al contexto historico -> hook-store (guardar turno)

*Flujo sin extensión:*

Session_start -> [prompt_usuario] -> API -> Respuesta contextualizada bajo la sesión. -> save_memory (guardar memorias en deepseek.md(no optimo))

**mem0 y Espacio Vectorial**

Esta es la parte más fascinante del proyecto, la implementación de base de datos de vectores.
Para implementar el sistema de memorias, mi agente sugirió utilizar mem0 porque lo confundió cuando le pregunté sobre que era el proyecto hermes (sin especificar que era HermesAgent de Neus). mem0 utiliza un modelo de inteligencia artificial ultra ligero (muy ligero, CPU con 100MB de ram) para convertir un string en datos vectoriales de más de 350 dimensiones. (una tupla gigante)

Usando ChromaBD gestionamos esos datos y junto los scripts del hook orquestamos un sistema que enriquece un prompt con contexto historico.
Para evitar que se inyectara recuerdos despues del primer turno se puso un flag que se dispara al inicio de cada sesión, junto con un máximo de caracteres de contexto de hasta 50k, para evitar que el modelo desborde de contexto.

Además se creó un sistema de memoria jerárquica, que da prioridad a los "recuerdos" más recientes en este orden: 

before_agent.py clasifica resultados por ventana temporal, prioriza lo reciente dando slots. 
- Corto plazo (<1h): 4 slots
- Medio plazo (1-24h): 3 slots
- Medio-largo plazo (1-7d): 2 slots
- Largo plazo (>7d): 1 slot

---

**Ahora el agente tiene 3 fuentes de memoria:**

1.-Deepseek.md -> Core, tunning, personalidad, aspectos clave.

2.-Contexto de la sesión -> Es lo que entiende mientras hablas con el CLI

3.-Recuerdos inyectados -> Memorias de turnos textuales en vectores

---
**Creditos:**

https://github.com/sluisr-dev/deepseek-cli fork de geminiCLI compatible con deepseek API

https://github.com/mem0ai/mem0 framework de capa de memoria
