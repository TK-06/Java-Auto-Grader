import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;

class Week3Test {

    @Test
    void addsTwoPositives() {
        Calculator calc = new Calculator();
        assertEquals(5, calc.add(2, 3));
    }

    @Test
    void addsNegatives() {
        Calculator calc = new Calculator();
        assertEquals(-1, calc.add(2, -3));
    }

    @Test
    void subtractsPositives() {
        Calculator calc = new Calculator();
        assertEquals(2, calc.subtract(5, 3));
    }

    @Test
    void subtractsToNegative() {
        Calculator calc = new Calculator();
        assertEquals(-2, calc.subtract(3, 5));
    }

    @Test
    void subtractsZero() {
        Calculator calc = new Calculator();
        assertEquals(5, calc.subtract(5, 0));
    }

    @Test
    @Disabled("demo of a skipped test - not counted toward tests_total")
    void notYetGraded() {
        Calculator calc = new Calculator();
        assertEquals(100, calc.add(1, 1));
    }
}
