// Development-only address evidence, not a game schema or a patch payload.
// Run against a read-only import. Full output belongs in build/save-research/.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import java.io.PrintWriter;

public class GhidraKremlinSaveInventory extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        boolean ru = args[1].equals("ru");
        if (!ru && !args[1].equals("en")) throw new IllegalArgumentException("ru or en required");
        String code = ru ? "1068:" : "1020:";
        String data = ru ? "1190:" : "10d0:";
        int jumpTable = ru ? 0x27a : 0x6ea;
        int[] descriptors = ru
            ? new int[] {0x502,0x50a,0x512,0x51a,0x522,0x52a,0x532,0x53a,0x542,0x54a,0x552,0x55a}
            : new int[] {0x512,0x51a,0x522,0x52a,0x532,0x53a,0x542,0x54a,0x552,0x55a,0x562,0x56a};
        try (PrintWriter out = new PrintWriter(args[0])) {
            out.println("program\t" + currentProgram.getName());
            out.println("language\t" + currentProgram.getLanguageID());
            out.println("native_class\tfactory_branch");
            for (int i = 0; i < 14; i++) {
                Address entry = toAddr(code + String.format("%04x", jumpTable + 2*i));
                int target = currentProgram.getMemory().getShort(entry) & 0xffff;
                out.printf("%d\t%s%04x%n", i, code, target);
            }
            out.println("descriptor\tword0\tword1\tword2\tword3\treferences");
            for (int offset : descriptors) {
                Address address = toAddr(data + String.format("%04x", offset));
                out.print(address);
                for (int i = 0; i < 4; i++)
                    out.printf("\t%04x", currentProgram.getMemory().getShort(address.add(2*i)) & 0xffff);
                for (int i = 0; i < 8; i++)
                    for (var ref : getReferencesFrom(address.add(i)))
                        out.print("\t+" + i + "=" + ref.getToAddress());
                out.println();
            }
        }
    }
}
