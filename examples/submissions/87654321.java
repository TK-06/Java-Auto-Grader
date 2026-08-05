public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }

    public int subtract(int a, int b) {
        // bug: student added instead of subtracting
        return a + b;
    }
}
