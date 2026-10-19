// Development-only export; full output belongs in ignored originals/source/.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import java.io.*;

public class GhidraPkrPerformance extends GhidraScript {
    public void run() throws Exception {
        File dir = new File(getScriptArgs()[0]);
        dir.mkdirs();
        // Virtual methods and hand-written render loops are missed by entry analysis.
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            if (!b.isExecute() || !b.isInitialized()) continue;
            byte[] bytes = new byte[(int)b.getSize()];
            b.getBytes(b.getStart(), bytes);
            for (int i = 0; i + 7 <= bytes.length; i++) {
                boolean prologue = (bytes[i] & 255) == 0x8c && (bytes[i+1] & 255) == 0xd0
                    && (bytes[i+2] & 255) == 0x90 && (bytes[i+3] & 255) == 0x45
                    && (bytes[i+4] & 255) == 0x55 && (bytes[i+5] & 255) == 0x8b
                    && (bytes[i+6] & 255) == 0xec;
                if (prologue || (i == 0 && b.getName().equals("Code3"))) {
                    var a = b.getStart().add(i);
                    if (getFunctionAt(a) == null) { disassemble(a); createFunction(a, null); }
                }
            }
        }
        DecompInterface dec = new DecompInterface();
        dec.openProgram(currentProgram);
        try (PrintWriter out = new PrintWriter(new File(dir, "listing.txt"));
             PrintWriter c = new PrintWriter(new File(dir, "decompiled.c"))) {
            out.println(currentProgram.getName() + " " + currentProgram.getLanguageID());
            for (MemoryBlock b : currentProgram.getMemory().getBlocks())
                out.println("BLOCK " + b.getName() + " " + b.getStart() + " " + b.getSize());
            FunctionIterator funcs = currentProgram.getFunctionManager().getFunctions(true);
            while (funcs.hasNext() && !monitor.isCancelled()) {
                Function f = funcs.next();
                out.println("FUNCTION " + f.getEntryPoint() + " " + f.getName());
                for (Instruction ins : currentProgram.getListing().getInstructions(f.getBody(), true))
                    out.println(ins.getAddress() + " " + ins);
                var result = dec.decompileFunction(f, 15, monitor);
                if (result.decompileCompleted()) {
                    c.println("/* " + f.getEntryPoint() + " */");
                    c.println(result.getDecompiledFunction().getC());
                }
            }
        } finally { dec.dispose(); }
        println("Exported performance analysis to " + dir);
    }
}
