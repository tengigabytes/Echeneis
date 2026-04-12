/**
 * SX1276 SPI Driver — Benchmark test fixture.
 *
 * This driver contains 6 intentionally planted bugs for code review testing.
 * A good code reviewer should identify all 6 issues.
 */

#include <stdint.h>
#include <stdbool.h>
#include "sx1276.h"
#include "spi.h"

#define SX1276_REG_FIFO          0x00
#define SX1276_REG_OP_MODE       0x01
#define SX1276_REG_FR_MSB        0x06
#define SX1276_REG_FR_MID        0x07
#define SX1276_REG_FR_LSB        0x08
#define SX1276_REG_PA_CONFIG     0x09
#define SX1276_REG_LNA           0x0C
#define SX1276_REG_FIFO_ADDR_PTR 0x0D
#define SX1276_REG_FIFO_TX_BASE  0x0E
#define SX1276_REG_FIFO_RX_BASE  0x0F
#define SX1276_REG_IRQ_FLAGS     0x12
#define SX1276_REG_RX_NB_BYTES   0x13
#define SX1276_REG_MODEM_CONFIG1 0x1D
#define SX1276_REG_MODEM_CONFIG2 0x1E

#define SX1276_MODE_SLEEP        0x00
#define SX1276_MODE_STANDBY      0x01
#define SX1276_MODE_TX           0x03
#define SX1276_MODE_RXCONTINUOUS 0x05
#define SX1276_MODE_RXSINGLE     0x06

#define SX1276_IRQ_TX_DONE       0x08
#define SX1276_IRQ_RX_DONE       0x40
#define SX1276_IRQ_CRC_ERROR     0x20

#define FXOSC  32000000   /* 32 MHz crystal */
#define FSTEP  (FXOSC / 524288)  /* BUG 1: Integer division truncation.
                                  * Should be: (double)FXOSC / 524288
                                  * or use 64-bit: FXOSC * 1ULL / 524288
                                  * This silently loses precision in freq calc. */

static spi_handle_t *spi;

void sx1276_init(spi_handle_t *spi_dev)
{
    spi = spi_dev;

    /* Set to sleep mode first */
    sx1276_write_reg(SX1276_REG_OP_MODE, SX1276_MODE_SLEEP);

    /* Enable LoRa mode (bit 7 of RegOpMode) */
    uint8_t mode = sx1276_read_reg(SX1276_REG_OP_MODE);
    mode |= 0x80;  /* Set LoRa bit */
    sx1276_write_reg(SX1276_REG_OP_MODE, mode);

    /* Return to standby */
    sx1276_write_reg(SX1276_REG_OP_MODE, SX1276_MODE_STANDBY | 0x80);
}

uint8_t sx1276_read_reg(uint8_t addr)
{
    uint8_t tx[2] = { addr & 0x7F, 0x00 };
    uint8_t rx[2];
    spi_transfer(spi, tx, rx, 2);
    return rx[1];
}

void sx1276_write_reg(uint8_t addr, uint8_t value)
{
    uint8_t tx[2] = { addr | 0x80, value };
    spi_transfer(spi, tx, NULL, 2);
}

void sx1276_set_frequency(uint32_t freq_hz)
{
    /* BUG 2: Using FSTEP (integer-truncated) causes accumulated error.
     * For 915 MHz: should be 0xE4C026 but calculates differently
     * due to integer division in FSTEP. */
    uint32_t frf = freq_hz / FSTEP;

    sx1276_write_reg(SX1276_REG_FR_MSB, (frf >> 16) & 0xFF);
    sx1276_write_reg(SX1276_REG_FR_MID, (frf >> 8) & 0xFF);
    sx1276_write_reg(SX1276_REG_FR_LSB, frf & 0xFF);
}

void sx1276_set_tx_power(int8_t power)
{
    if (power > 20) power = 20;
    if (power < 2)  power = 2;

    /* BUG 3: PA_BOOST selected (bit 7 = 1) but MaxPower bits [6:4] are
     * left as 0. Should set MaxPower for proper operation.
     * Correct: 0x80 | (0x07 << 4) | (power - 2)
     * Current code only sets PaSelect + OutputPower, missing MaxPower. */
    sx1276_write_reg(SX1276_REG_PA_CONFIG, 0x80 | (power - 2));
}

void sx1276_set_spreading_factor(uint8_t sf)
{
    if (sf < 6)  sf = 6;
    if (sf > 12) sf = 12;

    uint8_t config2 = sx1276_read_reg(SX1276_REG_MODEM_CONFIG2);
    config2 = (config2 & 0x0F) | (sf << 4);
    sx1276_write_reg(SX1276_REG_MODEM_CONFIG2, config2);
}

int sx1276_send_packet(const uint8_t *data, uint8_t len)
{
    if (len > 256) return -1;  /* BUG 4: Off-by-one. FIFO is 256 bytes but
                                * max payload is 255 (len is uint8_t so max is
                                * 255, but the check should be len > 255 for
                                * the documented 256-byte FIFO with 1 byte
                                * for length prefix). Actually the real bug is
                                * that uint8_t can never be > 256, so this
                                * check is always false — dead code. */

    /* Set FIFO pointer to TX base */
    sx1276_write_reg(SX1276_REG_FIFO_ADDR_PTR,
                     sx1276_read_reg(SX1276_REG_FIFO_TX_BASE));

    /* Write payload to FIFO */
    for (uint8_t i = 0; i < len; i++) {
        sx1276_write_reg(SX1276_REG_FIFO, data[i]);
    }

    /* Switch to TX mode */
    sx1276_write_reg(SX1276_REG_OP_MODE, SX1276_MODE_TX | 0x80);

    /* BUG 5: Busy-wait for TX done without timeout.
     * If TX never completes (e.g. antenna disconnected), this
     * hangs forever. Should have a timeout mechanism. */
    while (!(sx1276_read_reg(SX1276_REG_IRQ_FLAGS) & SX1276_IRQ_TX_DONE)) {
        /* spin */
    }

    /* Clear TX done flag */
    sx1276_write_reg(SX1276_REG_IRQ_FLAGS, SX1276_IRQ_TX_DONE);

    return 0;
}

int sx1276_receive_packet(uint8_t *buf, uint8_t buf_size)
{
    /* Set to RX single mode */
    sx1276_write_reg(SX1276_REG_OP_MODE, SX1276_MODE_RXSINGLE | 0x80);

    /* Wait for RX done */
    uint8_t irq;
    while (!((irq = sx1276_read_reg(SX1276_REG_IRQ_FLAGS)) & SX1276_IRQ_RX_DONE)) {
        /* spin — same timeout issue as TX */
    }

    /* BUG 6: CRC error check is done AFTER reading data instead of BEFORE.
     * If CRC fails, we still copy corrupted data to buf and only then
     * check. Should check CRC flag first, clear IRQ, and return error
     * before touching the FIFO. */

    /* Read packet length */
    uint8_t rx_len = sx1276_read_reg(SX1276_REG_RX_NB_BYTES);
    if (rx_len > buf_size) rx_len = buf_size;

    /* Set FIFO pointer to RX start */
    sx1276_write_reg(SX1276_REG_FIFO_ADDR_PTR,
                     sx1276_read_reg(SX1276_REG_FIFO_RX_BASE));

    /* Read data from FIFO */
    for (uint8_t i = 0; i < rx_len; i++) {
        buf[i] = sx1276_read_reg(SX1276_REG_FIFO);
    }

    /* Now check for CRC error — too late! */
    if (irq & SX1276_IRQ_CRC_ERROR) {
        sx1276_write_reg(SX1276_REG_IRQ_FLAGS, SX1276_IRQ_CRC_ERROR | SX1276_IRQ_RX_DONE);
        return -1;
    }

    /* Clear RX done flag */
    sx1276_write_reg(SX1276_REG_IRQ_FLAGS, SX1276_IRQ_RX_DONE);

    return rx_len;
}
