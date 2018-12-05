#!/usr/bin/python
# -*- coding: iso-8859-1 -*-

import json
import sys
import os
import imp
import subprocess
import shutil
import argparse
import datetime
import timeit
import multiprocessing

GIT_REPOSITORY = "https://github.com/blaizard/irapp.git"
EXECUTABLE_PATH = os.path.realpath(__file__)
EXECUTABLE_DIRECTORY_PATH = os.path.realpath(os.path.dirname(__file__))
EXECUTABLE_NAME = os.path.basename(__file__)
DEPENDENCIES_PATH = os.path.join(EXECUTABLE_DIRECTORY_PATH, ".irapp")
TEMP_DIRECTORY_PATH = os.path.join(EXECUTABLE_DIRECTORY_PATH, ".irapp/temp")
ASSETS_DIRECTORY_PATH = os.path.join(EXECUTABLE_DIRECTORY_PATH, ".irapp/assets")
DEFAULT_CONFIG_FILE = ".irapp.json"

"""
Load the necessary dependencies
"""
def loadDependencies():
	try:
		if not os.path.isdir(DEPENDENCIES_PATH):
			raise Exception("Missing tool dependencies at %s" % (DEPENDENCIES_PATH))
		irapp = imp.load_module("irapp", None, DEPENDENCIES_PATH, ('', '', imp.PKG_DIRECTORY))
		if not irapp:
			raise Exception("Could not load dependencies")
	except Exception as e:
		lib.error("%s, abort." % (e))
		lib.info("Consider updating with '%s update'" % (EXECUTABLE_NAME))
		sys.exit(1)

	# Load the dependencies
	return irapp.loadModules(), irapp.getTypeList(), irapp.lib

"""
Read the configruation file and create it if it does not exists.
"""
def readConfig(args):
	global lib

	# Read the dependencies
	modules, types, lib = loadDependencies()

	config = {
		"types": types,
		"root": EXECUTABLE_DIRECTORY_PATH,
		"assets": ASSETS_DIRECTORY_PATH,
		"pimpl": {}
	}

	# Read the configuration
	try:
		f = open(args.configPath, "r")
		with f:
			try:
				configUser = json.load(f)
				config.update(configUser)
			except Exception as e:
				lib.error("Could not parse configuration file '%s'; %s" % (str(args.configPath), str(e)))
				sys.exit(1)
	except IOError:
		lib.warning("Could not open configuration file '%s', using default" % (str(args.configPath)))

	# Add the required module if to the type list
	requiredModuleList = args.modules.split(",") if args.modules else []
	for moduleId in requiredModuleList:
		if moduleId not in config["types"]:
			config["types"].append(moduleId)

	# Map and remove unsupported modules
	typeList = []
	for moduleId in config["types"]:
		isRequired = (moduleId in requiredModuleList)
		if moduleId not in modules:
			if isRequired:
				lib.error("Unknown required module '%s', abort" % (moduleId))
				sys.exit(1)
			lib.warning("Unknown module '%s', ignoring" % (moduleId))
			continue
		moduleClass = modules[moduleId]
		# Add module only if it checks correctly
		if moduleClass.check(config):
			typeList.append(moduleId)
			config["pimpl"][moduleId] = moduleClass
			# Update the default configuration
			defaultConfig = moduleClass.config()
			defaultConfig.update(config)
			config = defaultConfig
		elif isRequired:
			lib.error("Required module '%s' is not supported by this project, abort" % (moduleId))
			sys.exit(1)
	config["types"] = typeList

	# Initialize all the classes
	for moduleId, moduleClass in config["pimpl"].items():
		config["pimpl"][moduleId] = moduleClass(config)

	lib.info("Modules identified: %s" % (", ".join(config["types"])))
	return config

# ---- Supported actions -----------------------------------------------------

"""
Entry point for all action mapped to the supported and enabled modules.
"""
def action(args):
	# Read the configuration
	config = readConfig(args)

	lib.info("Running command '%s' in '%s'" % (str(args.command), str(config["root"])))
	for moduleId in config["types"]:
		if args.command == "init":
			# Clear the assets directory if it exists
			if os.path.isdir(ASSETS_DIRECTORY_PATH):
				shutil.rmtree(ASSETS_DIRECTORY_PATH)
			config["pimpl"][moduleId].init()
		elif args.command == "build":
			config["pimpl"][moduleId].build(args.type)
		elif args.command == "clean":
			config["pimpl"][moduleId].clean()

"""
Print information regarding the program and loaded modules
"""
def info(args):
	# Read the configuration
	config = readConfig(args)

	lib.info("Hash: %s" % (str(getCurrentHash())))
	for moduleId in config["types"]:
		config["pimpl"][moduleId].info()

lock = multiprocessing.Lock()
def runWorker(command, hideOutput, iterationId):
	global lock

	start = timeit.default_timer()
	try:
		lib.shell(command, captureStdout=hideOutput)
	except BaseException as e:
		with lock:
			print(e)
			raise Exception()
	return (timeit.default_timer() - start), iterationId

"""
Run the program specified
"""
def run(args):
	global lock

	# Read the configuration
	config = readConfig(args)

	# Build the list of commands to be executed
	commandList = [cmd.split() for cmd in args.commandList] + ([args.args] if args.args else [])

	# Number of iterations to be performed (0 for endless)
	totalIterations = args.iterations if args.iterations else (0 if args.endless else (0 if args.time else 1))

	optionsStrList = []
	if totalIterations > 1:
		optionsStrList.append("%i iterations" % (totalIterations))
	elif totalIterations == 0:
		optionsStrList.append("endless mode")
	if args.time:
		optionsStrList.append("%is" % (args.time))
	optionsStr = " [%s]" % (", ".join(optionsStrList)) if len(optionsStrList) else ""

	if len(commandList) == 1:
		lib.info("Running command '%s'%s" % (" ".join(commandList[0]), optionsStr))
	elif len(commandList) > 1:
		lib.info("Running commands %s%s" % (", ".join([("'" + " ".join(cmd) + "'") for cmd in commandList]), optionsStr))
	else:
		lib.error("No command was executed")
		sys.exit(1)

	# Pre run the supported modules
	for moduleId in config["types"]:
		config["pimpl"][moduleId].runPre(commandList)

	# Create the pool of workers
	workerList = [None for i in range(args.nbJobs)]
	workerPool = multiprocessing.Pool(args.nbJobs)

	hideOutput = (totalIterations != 1) and not args.verbose
	startingTime = datetime.datetime.now()
	totalTimeS = 0
	nbIterations = 0
	nbWorkers = 0
	commandIndex = 0
	curIteration = 0
	iterations = {}
	while True:

		# Fill the worker pool and update the number of workers currently working
		nbWorkers = 0
		for i in range(args.nbJobs):

			# If a worker is registered
			if workerList[i]:
				if workerList[i].ready():
					if workerList[i].successful():
						# The worker is completed
						workerElpasedTimeS, workerIteration = workerList[i].get()
						workerList[i] = None
						# Increase the iteration number and check if one full iteration is completed
						iterations[workerIteration] = ({"nb": iterations[workerIteration]["nb"] + 1, "time": iterations[workerIteration]["time"] + workerElpasedTimeS}) if workerIteration in iterations else {"nb": 1, "time": workerElpasedTimeS}
						if iterations[workerIteration]["nb"] == len(commandList):
							nbIterations += 1
							totalTimeS += iterations[workerIteration]["time"]
							iterations.pop(workerIteration)
					else:
						# One of the worker failed
						workerPool.terminate()
						print("")
						sys.exit(1)
				else:
					nbWorkers += 1

			# If not registered, add it
			if not workerList[i]:
				workerList[i] = workerPool.apply_async(runWorker, (commandList[commandIndex], hideOutput, curIteration))
				# Increase the command index and the interation
				commandIndex += 1
				if commandIndex == len(commandList):
					commandIndex = 0
					curIteration += 1
				nbWorkers += 1

		if hideOutput:
			with lock:
				sys.stdout.write("\rTime: %s, %i iteration(s), average %s, %i job(s)" % (
						str(datetime.datetime.now() - startingTime),
						nbIterations,
						str(float("%.6f" % (totalTimeS / nbIterations))) + "s" if nbIterations else "?",
						nbWorkers))
				sys.stdout.flush()

		# If time reached its limit
		if args.time and (datetime.datetime.now() - startingTime).total_seconds() > args.time:
			break

		# If the number of iterations reached its limit
		if totalIterations and (nbIterations >= totalIterations):
			break

	# Ensure the workers are terminated
	workerPool.terminate()
	print("")

	# Post run the supported modules
	for moduleId in config["types"]:
		config["pimpl"][moduleId].runPost(commandList)

"""
Return the current hash or None if not available
"""
def getCurrentHash():
	hashPath = os.path.join(DEPENDENCIES_PATH, ".hash")
	if os.path.isfile(hashPath):
		with open(hashPath, "r") as f:
			return f.read()
	return None

"""
Updating the tool
"""
def update(args):

	# Read the current hash
	currentGitHash = getCurrentHash()
	lib.info("Current version: %s" % (str(currentGitHash)))

	if not args.force:
		gitRemote = lib.shell(["git", "ls-remote", GIT_REPOSITORY, "HEAD"], captureStdout=True)
		gitHash = gitRemote[0].lstrip().split()[0]
		lib.info("Latest version available: %s" % (gitHash))

		# Need to update
		if currentGitHash == gitHash:
			lib.info("Already up to date!")
			return
	lib.info("Updating to latest version...")

	# Create and cleanup the temporary directory
	if os.path.isdir(TEMP_DIRECTORY_PATH):
		shutil.rmtree(TEMP_DIRECTORY_PATH)
	if not os.path.exists(TEMP_DIRECTORY_PATH):
		os.makedirs(TEMP_DIRECTORY_PATH)

	# Clone the latest repository into this path
	lib.shell(["git", "clone", GIT_REPOSITORY, "."], cwd=TEMP_DIRECTORY_PATH)

	# Read the version changes
	changeList = None
	if currentGitHash:
		changeList = lib.shell(["git", "log", "--pretty=\"%s\"", "%s..HEAD" % (currentGitHash)], cwd=TEMP_DIRECTORY_PATH, captureStdout=True, ignoreError=True) 

	# These are the location of the files for the updated and should NOT change over time
	executableName = "app.py"
	dependenciesDirectoryName = ".irapp"

	# Remove everything inside the dependencies path, except the current temp directory
	tempFullPath = os.path.realpath(TEMP_DIRECTORY_PATH)
	for root, dirs, files in os.walk(DEPENDENCIES_PATH):
		for name in files:
			os.remove(os.path.join(root, name))

		for name in dirs:
			fullPath = os.path.realpath(os.path.join(root, name))
			if not tempFullPath.startswith(fullPath):
				shutil.rmtree(fullPath)
			# Do not look into this path
			if tempFullPath == fullPath:
				dirs.remove(name)

	# Move the content of the dependencies directory into the new one
	dependenciesDirectoryPath = os.path.join(TEMP_DIRECTORY_PATH, dependenciesDirectoryName)
	dependenciesContentFiles = os.listdir(dependenciesDirectoryPath)
	for fileName in dependenciesContentFiles:
		shutil.move(os.path.join(dependenciesDirectoryPath, fileName), DEPENDENCIES_PATH)

	# Replace main source file
	shutil.move(os.path.join(TEMP_DIRECTORY_PATH, executableName), EXECUTABLE_PATH)

	# Read again current hash
	gitRawHash = lib.shell(["git", "rev-parse", "HEAD"], cwd=TEMP_DIRECTORY_PATH, captureStdout=True)
	gitHash = gitRawHash[0].lstrip().split()[0]

	# Remove temporary directory
	try:
		shutil.rmtree(TEMP_DIRECTORY_PATH)
	except Exception as e:
		lib.warning("Could not delete %s, %s" % (TEMP_DIRECTORY_PATH, e))

	# Create hash file
	with open(os.path.join(DEPENDENCIES_PATH, ".hash"), "w") as f:
		f.write(gitHash)

	lib.info("Update to %s succeed." % (gitHash))
	if changeList:
		lib.info("Change(s):")
		for change in changeList:
			lib.info("- %s" % (change))

# ---- Lib implementation -----------------------------------------------------

"""
Minimalistic lib implementation as a fallback
"""
class lib:
	@staticmethod
	def info(message, type = "INFO"):
		print("[%s] %s" % (str(type), str(message)))

	@staticmethod
	def warning(message):
		lib.info(message, type = "WARNING")

	@staticmethod
	def error(message):
		lib.info(message, type = "ERROR")

	"""
	Execute a shell command in a specific directory.
	If it fails, it will throw.
	"""
	@staticmethod
	def shell(command, cwd=".", captureStdout=False, ignoreError=False):
		proc = subprocess.Popen(command, cwd=cwd, shell=False, stdout=(subprocess.PIPE if captureStdout else None),
				stderr=(subprocess.PIPE if captureStdout else subprocess.STDOUT))

		output = []
		exception = False
		try:
			if proc.stdout:
				for line in iter(proc.stdout.readline, b''):
					line = line.rstrip().decode('utf-8')
					output.append(line)
			out, error = proc.communicate()
		except BaseException as e:
			exception = e

		if (proc.returncode != 0) or exception:
			extra = str(exception.__class__.__name__) if exception else "errno=%s" % (str(proc.returncode))
			message = "Fail to execute '%s' in '%s' (%s)" % (" ".join(command), str(cwd), extra)
			if ignoreError:
				lib.warning(message)
				output = []
			else:
				for line in output:
					print(line)
				raise Exception(message)

		return output

# -----------------------------------------------------------------------------

"""
Entry point fo the script
"""
if __name__ == "__main__":

	commandActions = {
		"info": info,
		"init": action,
		"clean": action,
		"build": action,
		"run": run,
		"update": update
	}

	parser = argparse.ArgumentParser(description = "Application helper script.")
	parser.add_argument("-c", "--config", action="store", dest="configPath", default=DEFAULT_CONFIG_FILE, help="Path of the build definition (default=%s)." % (DEFAULT_CONFIG_FILE))
	parser.add_argument("-v", "--version", action='version', version="%s hash: %s" % (os.path.basename(__file__), str(getCurrentHash())))
	parser.add_argument("-m", "--modules", action='store', dest="modules", default="", help="Enforce the use of specific modules (comma separated). If one of the given module fails to setup, the program returns with an error.")

	subparsers = parser.add_subparsers(dest="command", help='List of available commands.')

	parserRun = subparsers.add_parser("run", help='Execute the specified commmand.')
	parserRun.add_argument("-e", "--endless", action="store_true", dest="endless", default=False, help="Run the command endlessly (until it fails).")
	parserRun.add_argument("-v", "--verbose", action="store_true", dest="verbose", default=False, help="Force printing the output while running the command.")
	parserRun.add_argument("-j", "--jobs", type=int, action="store", dest="nbJobs", default=1, help="Number of jobs to run in parallel.")
	parserRun.add_argument("-c", "--cmd", action="append", dest="commandList", default=[], help="Command to be executed. More than one command can be executed simultaneously sequentially. If combined with --jobs the commands will be executed simultaneously.")
	parserRun.add_argument("-i", "--iterations", type=int, action="store", dest="iterations", default=0, help="Number of iterations to be performed.")
	parserRun.add_argument("-t", "--time", type=int, action="store", dest="time", default=0, help="Run the commands for a specific amount of time (in seconds).")
	parserRun.add_argument("args", nargs=argparse.REMAINDER, help='Extra arguments to be passed to the command executed.')

	subparsers.add_parser("info", help='Display information about the script and the loaded modules.')
	subparsers.add_parser("init", help='Initialize or setup the project environment.')
	subparsers.add_parser("clean", help='Clean the project environment from build artifacts.')
	parserBuild = subparsers.add_parser("build", help='Build the project.')
	parserBuild.add_argument("type", action='store', nargs='?', help='Build type.')

	parserUpdate = subparsers.add_parser("update", help='Update the tool to the latest version available.')
	parserUpdate.add_argument("-f", "--force", action="store_true", dest="force", default=False, help="If set, it will update even if the last version is detected.")

	args = parser.parse_args()

	# Excecute the action
	fct = commandActions.get(args.command, None)
	if fct == None:
		lib.error("Invalid command '%s'" % (str(args.command)))
		parser.print_help()
		sys.exit(1)

	# Execute the proper action
	fct(args)