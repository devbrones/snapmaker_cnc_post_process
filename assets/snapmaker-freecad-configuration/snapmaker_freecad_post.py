# ***************************************************************************
# *   (c) sliptonic (shopinthewoods@gmail.com) 2014                        *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful,            *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Lesser General Public License for more details.                   *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with FreeCAD; if not, write to the Free Software        *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************/
from __future__ import print_function
import FreeCAD
from FreeCAD import Units
import Path
import argparse
import datetime
import shlex
import Path.Post.Utils as PostUtils
from PathScripts import PathUtils
import Path.Log as PathLog
import Path.Geom as PathGeom
import math
import base64
print("successfully imported FreeCAD modules")

LOG_MODULE = PathLog.thisModule()
PathLog.setLevel(PathLog.Level.ERROR, LOG_MODULE)

TOOLTIP = '''
This is a postprocessor file for the Path workbench. It is used to
take a pseudo-gcode fragment outputted by a Path object, and output
real GCode suitable for a linuxcnc 3 axis mill. This postprocessor, once placed
in the appropriate PathScripts folder, can be used directly from inside
FreeCAD, via the GUI importer or via python scripts with:

import linuxcnc_post
linuxcnc_post.export(object,"/path/to/file.ncc","")
'''

now = datetime.datetime.now()

parser = argparse.ArgumentParser(prog='snapmaker_freecad', add_help=False)
parser.add_argument('--no-header', action='store_true', help='suppress header output')
parser.add_argument('--no-comments', action='store_true', help='suppress comment output')
parser.add_argument('--line-numbers', action='store_true', help='prefix with line numbers')
parser.add_argument('--no-show-editor', action='store_true', help='don\'t pop up editor before writing output')
parser.add_argument('--precision', default='3', help='number of digits of precision, default=3')
parser.add_argument('--segments', default='10', help='segments in curved paths: segs/cm, default=40')
parser.add_argument('--break-straight', action='store_true', help='breaks also straight paths same resolution as in curved paths')
parser.add_argument('--preamble', help='set commands to be issued before the first command, default="G17\nG90"')
parser.add_argument('--postamble', help='set commands to be issued after the last command, default="M05\nG17 G90\nM2"')
parser.add_argument('--inches', action='store_true', help='Convert output for US imperial mode (G20)')
parser.add_argument('--modal', action='store_true', help='Output the Same G-command Name USE NonModal Mode')
parser.add_argument('--axis-modal', action='store_true', help='Output the Same Axis Value Mode')
parser.add_argument('--no-tlo', action='store_true', help='suppress tool length offset (G43) following tool changes')
parser.add_argument('--leveltwocnc', action='store_true', help='Use the new Snapmaker 200W cnc toolhead')

TOOLTIP_ARGS = parser.format_help()

# GCodes and GCode parameters =================================================

CMD_MOVE_LINEAR_RAPID = 'G0' # moves linearly to a point
CMD_MOVE_LINEAR       = 'G1'
CMD_MOVE_ARC_CW       = 'G2'
CMD_MOVE_ARC_CCW      = 'G3'
CMD_MOVE_BEZIER       = 'G5'
CMD_SPINDLE_ON        = 'M3' # variable spindle speed, needs M3 <0-100%>
CMD_SPINDLE_OFF       = 'M5'
CMD_HOLE_SIMPLE       = 'G81'
CMD_HOLE_DWELL        = 'G82' 
CMD_HOLE_PECKED       = 'G83' 

MOVE_COMMANDS = [ #this is a hoorrrriiibbleee way of doing it but i dont care:D
    CMD_MOVE_LINEAR_RAPID +" ",
    CMD_MOVE_LINEAR + " ",
    CMD_MOVE_ARC_CW + " ",
    CMD_MOVE_ARC_CCW + " ",
    CMD_MOVE_BEZIER + " ",
    CMD_HOLE_SIMPLE + " ",
    CMD_HOLE_DWELL + " ",
    CMD_HOLE_PECKED + " "       
]

# Param: Movement
P_POSITION_X = 'X'
P_POSITION_Y = 'Y'
P_POSITION_Z = 'Z'
P_FEEDRATE = 'F'

# Param: Spindle
P_SPINDLE_RPM = 'S'
P_SPINDLE_POWER = 'P'

# Param: Drilling
P_DRILL_RETRACT_HEIGHT = 'R'
P_PECK_DEPTH = 'Q' # Q = peck distance
P_DWELL_MS = 'P' # P (ms), S (sec) = Pause time in finished hole
P_DWELL_S = 'S'

# =============================================================================
# Machine specific constants
class SMCNC:
    header = "standardCNCToolheadForSM2"
    minSpindleRPM = 6000
    minSpindlePower = 50
    maxSpindleRPM = 12000
    maxSpindlePower = 100

class ARCNC:
    header = "levelTwoCNCToolheadForSM2"
    minSpindleRPM = 8000
    minSpindlePower = 0
    maxSpindleRPM = 18000
    maxSpindlePower = 100


# =============================================================================
moveDrillInRetractHeight = False
holeRetractionFactor = 10 # vertical feedrate factor on retract. To speed up retraction make factor > 1

# =============================================================================
# Default move speeds if nothing else is set
feedrateHorizontal = 600
feedrateVertical = 300
# =============================================================================
# Commands that are not supported by Marlin (movement is simulated)
commandsToSimulate = [CMD_HOLE_SIMPLE, CMD_HOLE_PECKED, CMD_HOLE_DWELL]
commandsToConvert = [CMD_MOVE_ARC_CW, CMD_MOVE_ARC_CCW, CMD_MOVE_LINEAR_RAPID, CMD_MOVE_LINEAR, CMD_MOVE_BEZIER]

# =============================================================================

# These globals set common customization preferences
OUTPUT_COMMENTS = True
OUTPUT_HEADER = True
OUTPUT_LINE_NUMBERS = False
SHOW_EDITOR = True
MODAL = False  # if true commands are suppressed if the same as previous line.
USE_TLO = True # if true G43 will be output following tool changes
OUTPUT_DOUBLES = True  # if false duplicate axis values are suppressed if the same as previous line.
COMMAND_SPACE = " "

LINENR = 100  # line number starting value
SEGMENTS_PER_CM_ARC = 40 # Numbers of segments an arc should be broken into within 1cm of arc distance
BREAK_STRAIGHTS = False # When True, straight lines will be broken into subsegments as curved paths

# These globals will be reflected in the Machine configuration of the project
UNITS = "G21"  # G21 for metric, G20 for us standard
UNIT_SPEED_FORMAT = 'mm/min'
UNIT_FORMAT = 'mm'

MACHINE_NAME = "Snapmaker"
CORNER_MIN = {'x': 0, 'y': 0, 'z': 0}
CORNER_MAX = {'x': 500, 'y': 300, 'z': 300}
PRECISION = 3

# Preamble text will appear at the beginning of the GCODE output file.
PREAMBLE = '''G17 G54 G40 G49 G80 G90'''

# Postamble text will appear following the last operation.
POSTAMBLE = CMD_SPINDLE_OFF + "\n"
# Pre operation text will be inserted before every operation
PRE_OPERATION = ''''''

# Post operation text will be inserted after every operation
POST_OPERATION = ''''''

# Tool Change commands will be inserted before a tool change
TOOL_CHANGE = ''''''

currentHeadPosition = FreeCAD.Vector(0,0,0)

TOOLHEAD = None

# to distinguish python built-in open function from the one declared below
if open.__module__ in ['__builtin__','io']:
    pythonopen = open

def log(msg):
    PathLog.debug(msg)

def warn(msg):
    PathLog.warning(msg)

def err(msg):
    PathLog.error(msg)

def processArguments(argstring):
    # pylint: disable=global-statement
    global OUTPUT_HEADER
    global OUTPUT_COMMENTS
    global OUTPUT_LINE_NUMBERS
    global SHOW_EDITOR
    global PRECISION
    global PREAMBLE
    global POSTAMBLE
    global UNITS
    global UNIT_SPEED_FORMAT
    global UNIT_FORMAT
    global MODAL
    global USE_TLO
    global OUTPUT_DOUBLES
    global SEGMENTS_PER_CM_ARC
    global BREAK_STRAIGHTS
    global TOOLHEAD
    global MOVE_COMMANDS

    try:
        args = parser.parse_args(shlex.split(argstring))
        if args.no_header:
            OUTPUT_HEADER = False
        if args.no_comments:
            OUTPUT_COMMENTS = False
        if args.line_numbers:
            OUTPUT_LINE_NUMBERS = True
        if args.no_show_editor:
            SHOW_EDITOR = False
        print("Show editor = %d" % SHOW_EDITOR)
        PRECISION = args.precision

        SEGMENTS_PER_CM_ARC = min(max(float(args.segments), 1), 100)
        print("SEG/ARC:: "+ str(SEGMENTS_PER_CM_ARC))
        if args.preamble is not None:
            PREAMBLE = args.preamble
        if args.postamble is not None:
            POSTAMBLE = args.postamble
        if args.inches:
            UNITS = 'G20'
            UNIT_SPEED_FORMAT = 'in/min'
            UNIT_FORMAT = 'in'
            PRECISION = 4
        if args.modal:
            MODAL = True
        if args.no_tlo:
            USE_TLO = False
        if args.axis_modal:
            print ('here')
            OUTPUT_DOUBLES = False
        if args.break_straight:
            BREAK_STRAIGHTS = True
        if args.leveltwocnc:
            TOOLHEAD = ARCNC()
        else:
            TOOLHEAD = SMCNC()

    except Exception: # pylint: disable=broad-except
        return False

    return True


def export(objectslist, filename, argstring):
    # pylint: disable=global-statement
    if not processArguments(argstring):
        return None
    global UNITS
    global UNIT_FORMAT
    global UNIT_SPEED_FORMAT

    for obj in objectslist:
        if not hasattr(obj, "Path"):
            print("the object " + obj.Name + " is not a path. Please select only path and Compounds.")
            return None

    print("postprocessing...")
    gcode = ""

    # prepare to take picture
    FreeCAD.Gui.activeDocument().activeView().viewIsometric()
    FreeCAD.Gui.SendMsgToActiveView("ViewFit")
    imagePath = FreeCAD.Gui.activeDocument().Document.FileName + ".png"
    FreeCAD.Gui.activeDocument().activeView().saveImage(imagePath,720,480,'White')

    imageBase64 = ""
    with open(imagePath, "rb") as image_file:
        imageBase64 = base64.b64encode(image_file.read())

    # write header
    if OUTPUT_HEADER:
        gcode += linenumber() + ";Exported for Snapmaker 2\n"
        gcode += linenumber() + ";Post Processor: " + __name__ + "\n"
        gcode += linenumber() + ";Output Time:" + str(now) + "\n"
        if not imageBase64 == "":
            gcode += linenumber() + ";Header Start\n;header_type: cnc\n;tool_head: " + TOOLHEAD.header + "\n;machine: "+ MACHINE_NAME +"\n;gcode_flavor: marlin" "\n;thumbnail: data:image/png;base64,"+ imageBase64.decode() + "\n;Header End\n"
        gcode += linenumber() + PREAMBLE + "\n"        
        gcode += linenumber() + "G0 Z10.00 F300" + "\n"
        # gcode += linenumber() + "G0 Z0.50 F120" + "\n"
        PathLog.debug("===== Post-process for Snapmaker 2 (export linear moves only) =====\n")

    gcode += linenumber() + UNITS + "\n"

    for obj in objectslist:

        # Skip inactive operations
        if hasattr(obj, 'Active'):
            if not obj.Active:
                continue
        if hasattr(obj, 'Base') and hasattr(obj.Base, 'Active'):
            if not obj.Base.Active:
                continue

        # fetch machine details
        job = PathUtils.findParentJob(obj)

        myMachine = 'not set'

        if hasattr(job, "MachineName"):
            myMachine = job.MachineName

        if hasattr(job, "MachineUnits"):
            if job.MachineUnits == "Metric":
                UNITS = "G21"
                UNIT_FORMAT = 'mm'
                UNIT_SPEED_FORMAT = 'mm/min'
            else:
                UNITS = "G20"
                UNIT_FORMAT = 'in'
                UNIT_SPEED_FORMAT = 'in/min'


        # get coolant mode
        coolantMode = 'None'
        if hasattr(obj, "CoolantMode") or hasattr(obj, 'Base') and  hasattr(obj.Base, "CoolantMode"):
            if hasattr(obj, "CoolantMode"):
                coolantMode = obj.CoolantMode
            else:
                coolantMode = obj.Base.CoolantMode

        # process the operation gcode
        gcode += parse(obj)

        # do the post_op
        if OUTPUT_COMMENTS:
            gcode += linenumber() + ";finish operation: %s\n" % obj.Label
        for line in POST_OPERATION.splitlines(True):
            gcode += linenumber() + line

    # do the post_amble
    if OUTPUT_COMMENTS:
        gcode += ";begin postamble\n"
    for line in POSTAMBLE.splitlines(True):
        gcode += linenumber() + line

    # generate boundary

    Xmoves=[]
    Ymoves=[]
    Zmoves=[]
    Bmoves=[0] # placeholder
    for line in gcode.split("\n"):
        #print(line)
        if any(move_command in line for move_command in MOVE_COMMANDS):
            command = line.split()
            # add all x,y,z position values to a list
            if len(list(filter(lambda x: "X" in x, command))):
                Xmoves.append(float(list(filter(lambda x: "X" in x, command))[0][1:]))
            if len(list(filter(lambda x: "Y" in x, command))):
                Ymoves.append(float(list(filter(lambda y: "Y" in y, command))[0][1:]))
            if len(list(filter(lambda x: "Z" in x, command))):
                Zmoves.append(float(list(filter(lambda z: "Z" in z, command))[0][1:]))

    #;max_x(mm): 35.512     # Example boundary headers
    #;max_y(mm): 318.811    # 
    #;max_z(mm): 80         # 
    #;max_b(mm): 0          # maybe b is rotary? have no idea
    #;min_x(mm): 20.232     # 
    #;min_y(mm): 315.667    # 
    #;min_b(mm): 0          # 
    #;min_z(mm): -2         # 

    # create boundary string
    bdry = f""";max_x(mm): {max(Xmoves)}
;max_y(mm): {max(Ymoves)}
;max_z(mm): {max(Zmoves)}
;max_b(mm): {max(Bmoves)}
;min_x(mm): {min(Xmoves)}
;min_y(mm): {min(Ymoves)}
;min_b(mm): {min(Bmoves)}
;min_z(mm): {min(Zmoves)}\n"""

    #add the boundary string to the preamble
    preamble_position = gcode.find(";thumbnail: data:image/png;base64") # will give the position of the first letter
    gcode = gcode[:preamble_position] + bdry + gcode[preamble_position:]


    if FreeCAD.GuiUp and SHOW_EDITOR:
        dia = PostUtils.GCodeEditorDialog()
        dia.editor.setText(gcode)
        result = dia.exec_()
        if result:
            final = dia.editor.toPlainText()
        else:
            final = gcode
    else:
        final = gcode

    print("done postprocessing.")

    if not filename == '-':
        gfile = pythonopen(filename, "w")
        gfile.write(final)
        gfile.close()

    return final


def linenumber():
    # pylint: disable=global-statement
    global LINENR
    if OUTPUT_LINE_NUMBERS is True:
        LINENR += 10
        return "N" + str(LINENR) + " "
    return ""


def createCommand(command, x, y, z, feedrate):
    precision_string = '.' + str(PRECISION) + 'f'

    xFormatted = format(float(x), precision_string)
    yFormatted = format(float(y), precision_string)
    zFormatted = format(float(z), precision_string)
    feedrateFormatted = format(float(feedrate), precision_string)
    return linenumber() + "{command} X{x} Y{y} Z{z} F{feedrate}\n".format(command=command, x=xFormatted, y=yFormatted, z=zFormatted, feedrate=feedrateFormatted)

def createNoPosCommand(command, params):
    return linenumber() + "{command} {params}\n".format(command=command, params=params)


def parse(pathobj):
    # pylint: disable=global-statement
    global PRECISION
    global MODAL
    global OUTPUT_DOUBLES
    global UNIT_FORMAT
    global UNIT_SPEED_FORMAT
    global SEGMENTS_PER_CM_ARC

    global feedrateHorizontal
    global feedrateVertical

    out = ""
    lastcommand = None
    precision_string = '.' + str(PRECISION) + 'f'

    # the order of parameters
    # linuxcnc doesn't want K properties on XY plane  Arcs need work.
    params = ['X', 'Y', 'Z', 'A', 'B', 'C', 'I', 'J', 'F', 'S', 'T', 'Q', 'R', 'L', 'H', 'D', 'P']
    firstmove = Path.Command("G0", {"X": -1, "Y": -1, "Z": -1, "F": 0.0})

    if hasattr(pathobj, "Group"):  # We have a compound or project.
        # if OUTPUT_COMMENTS:
        #     out += linenumber() + "(compound: " + pathobj.Label + ")\n"
        for p in pathobj.Group:            
            out += parse(p)
        return out
    else:  # parsing simple path
        log("=== " + pathobj.Name + "===")
        
        # groups might contain non-path things like stock.
        if not hasattr(pathobj, "Path"):
            return out

        for c in pathobj.Path.Commands:
            log("Next: " + str(c))

            outstring = []
            command = c.Name

            if command[0] == '(':
                outstring.append(';' + command + '\n')
                continue

            # if modal: suppress the command if it is the same as the last one
            if MODAL is True:
                if command == lastcommand:
                    outstring.pop(0)

            # Find feedrate and assign to either horizontal or vertical speed
            if P_FEEDRATE in c.Parameters:
                readFeedrate = c.Parameters[P_FEEDRATE]

                speed = Units.Quantity(readFeedrate, FreeCAD.Units.Velocity)
                feedrateString = 0
                if speed.getValueAs(UNIT_SPEED_FORMAT) > 0.0:
                    feedrateString = float(speed.getValueAs(UNIT_SPEED_FORMAT))

                if feedrateString > 0:
                    if P_POSITION_Z in c.Parameters:
                        if not c.Parameters[P_POSITION_Z] == currentHeadPosition.z:
                            if not feedrateVertical == feedrateString:
                                log("New Vertical Feedrate " + str(feedrateString))
                            feedrateVertical = feedrateString                            
                        else:
                            if not feedrateHorizontal == feedrateString:
                                log("New Horizontal Feedrate " + str(feedrateString))
                            feedrateHorizontal = feedrateString
                            
                    else:
                        feedrateHorizontal = feedrateString
                        if not feedrateHorizontal == feedrateString:
                            log("New Horizontal Feedrate " + str(feedrateString))
                        log("New Horizontal Feedrate " + str(feedrateHorizontal))    

            if command in commandsToSimulate:
                if command == CMD_HOLE_SIMPLE or command == CMD_HOLE_DWELL or command == CMD_HOLE_PECKED:                   
                    posX = c.Parameters[P_POSITION_X]
                    posY = c.Parameters[P_POSITION_Y]
                    posZ = c.Parameters[P_POSITION_Z]
                    retractHeight = c.Parameters[P_DRILL_RETRACT_HEIGHT]

                    # In case a pecking statement is provided (usually G83)                    
                    peckDepth = 0
                    peckCount = 0
                    if P_PECK_DEPTH in c.Parameters:
                        peckDepth = c.Parameters[P_PECK_DEPTH]
                        totalToDrill = currentHeadPosition.z - posZ
                        warn("total drill:" +str(totalToDrill)+ " peckDepth: "+str(peckDepth))
                        peckCount = math.floor(totalToDrill/peckDepth)

                    # In case a dwell statement is provided (G82, G83)
                    dwellTimeMs = 0
                    if P_DWELL_MS in c.Parameters:
                        dwellTimeMs = float(c.Parameters[P_DWELL_MS])
                    if P_DWELL_S in c.Parameters:
                        dwellTimeMs = float(c.Parameters[P_DWELL_S] * 1000) 

                    cmdOverHole     = createCommand("G1", posX, posY, currentHeadPosition.z , feedrateHorizontal)
                    cmdEnteringHole = createCommand("G1", posX, posY, posZ , feedrateVertical)
                    cmdLeavingHole  = createCommand("G1", posX, posY, retractHeight , feedrateVertical*holeRetractionFactor)                   

                    # move over hole
                    outstring.append(cmdOverHole)

                    # do pecking moves if parameter was found
                    if peckCount > 0:
                        for peckNumber in range(peckCount):
                            commandPeck = createCommand("G1", posX, posY, currentHeadPosition.z - (peckNumber+1) * peckDepth , feedrateVertical)
                            outstring.append(commandPeck)
                            outstring.append(cmdLeavingHole)   

                    # move fully into hole
                    outstring.append(cmdEnteringHole)

                    # dwell in the finished hole if parameter was found
                    if dwellTimeMs > 0:
                        outstring.append(createNoPosCommand("G4","P"+str(dwellTimeMs)))

                    # leave the hole
                    outstring.append(cmdLeavingHole)      

                    if not moveDrillInRetractHeight:
                        outstring.append(cmdOverHole)                                              

            elif command in commandsToConvert:
                edgeOfCommand = PathGeom.edgeForCmd(c, FreeCAD.Vector(currentHeadPosition) )

                if not (edgeOfCommand == None):
                    requiredPointsForArc = 2 # only two points for all straight commands            

                    #break all non-straight commands
                    if c.Name not in ["G0", "G00", "G1", "G01"] or BREAK_STRAIGHTS:
                        requiredPointsForArc = 1 + math.ceil(edgeOfCommand.Length * (SEGMENTS_PER_CM_ARC / 10))

                    discretePoints = edgeOfCommand.copy().discretize(requiredPointsForArc)

                    for p in discretePoints[1:]:
                        # segmentCommand = "G1 X"+str(p.x)+" Y"+str(p.y)+" Z"+str(p.z)+"\n"

                        adaptiveFeedrate = min(feedrateVertical, feedrateHorizontal)
                        if P_POSITION_Z in c.Parameters:
                            posZ = c.Parameters[P_POSITION_Z]
                            if posZ == currentHeadPosition.z:
                                adaptiveFeedrate = feedrateHorizontal
                        else:
                            adaptiveFeedrate = feedrateHorizontal

                        segmentCommand = createCommand("G1",p.x, p.y, p.z, adaptiveFeedrate)
                        #log(segmentCommand)
                        outstring.append(segmentCommand)
                    log(" ▶ broke shape into " + str(len(discretePoints)) + " segments")
            elif command == CMD_SPINDLE_ON:
                rpmSet = int(c.Parameters[P_SPINDLE_RPM])
                powerToSet = rpmSet / TOOLHEAD.maxSpindleRPM * TOOLHEAD.maxSpindlePower
                if powerToSet < TOOLHEAD.minSpindlePower:
                    powerToSet = TOOLHEAD.minSpindlePower
                if powerToSet > TOOLHEAD.maxSpindlePower:
                    powerToSet = TOOLHEAD.maxSpindlePower 
                outstring.append(createNoPosCommand(CMD_SPINDLE_ON, P_SPINDLE_POWER + str(powerToSet)))
            else:
                outstring.append(command)     

            #remember the last position moved to
            if not (command == CMD_HOLE_SIMPLE or command == CMD_HOLE_DWELL or command == CMD_HOLE_PECKED):
                for param in c.Parameters:
                    if param == "X":
                        currentHeadPosition.x = c.Parameters[param]
                    if param == "Y":
                        currentHeadPosition.y = c.Parameters[param]
                    if param == "Z":
                        currentHeadPosition.z = c.Parameters[param]
            else:
                for param in c.Parameters:
                    if param == "X":
                        currentHeadPosition.x = c.Parameters[param]
                    if param == "Y":
                        currentHeadPosition.y = c.Parameters[param]
                if moveDrillInRetractHeight:
                    drillRetractHeight = c.Parameters[P_DRILL_RETRACT_HEIGHT]
                    currentHeadPosition.z = drillRetractHeight

            if command == "message":
                if OUTPUT_COMMENTS is False:
                    out = []
                else:
                    outstring.pop(0)  # remove the command

            # prepend a line number and append a newline
            if len(outstring) >= 1:
                if OUTPUT_LINE_NUMBERS:
                    outstring.insert(0, (linenumber()))

                # append the line to the final output
                for w in outstring:
                    out += w
                out = out.strip() + "\n"

        return out

print(__name__ + " gcode postprocessor loaded.")
