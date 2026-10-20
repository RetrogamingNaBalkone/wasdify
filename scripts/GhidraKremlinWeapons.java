// Read-only development analysis. Full exports belong in ignored directories.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.program.model.listing.*;
import java.io.*;

public class GhidraKremlinWeapons extends GhidraScript {
    public void run() throws Exception {
        DecompInterface dec = new DecompInterface();
        dec.openProgram(currentProgram);
        try (PrintWriter out = new PrintWriter(getScriptArgs()[0])) {
            for (String address : getScriptArgs()[1].split(",")) {
                var a = toAddr(address);
                Function f = getFunctionAt(a);
                if (f == null) { disassemble(a); f = createFunction(a, null); }
                out.println("FUNCTION " + a);
                for (Instruction ins : currentProgram.getListing().getInstructions(f.getBody(), true))
                    out.println(ins.getAddress() + " " + ins);
                var result = dec.decompileFunction(f, 30, monitor);
                if (result.decompileCompleted()) out.println(result.getDecompiledFunction().getC());
            }
        } finally { dec.dispose(); }
    }
}
